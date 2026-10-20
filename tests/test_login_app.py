import asyncio
import json
import shutil
from pathlib import Path

import pytest

from provepr import plan as planfile
from provepr import report, runner, server

APP = Path(__file__).parent / "login_app"
PASSWORD = "s3cret-pw"


def copy_app(tmp_path: Path) -> Path:
    app = tmp_path / "app"
    shutil.copytree(APP, app, ignore=shutil.ignore_patterns("runs"))
    return app / ".provepr" / "plan.yaml"


def test_setup_signs_in_once_and_each_claim_starts_clean(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGIN_PASSWORD", PASSWORD)
    run_dir, rep = runner.run_plan(copy_app(tmp_path))
    md = report.write(run_dir, rep)
    status = {c["id"]: c["status"] for c in rep["claims"]}
    assert status == dict.fromkeys(["signed-in", "reset-again", "logged-out", "widgets", "mobile"], "PASS"), md
    assert rep["setup"]["status"] == "PASS" and "setup: 5/5 steps passed" in md
    assert all(c["before_each"][0]["ok"] for c in rep["claims"])
    assert not (run_dir / "frames" / runner.SETUP_ID).exists()  # a passing setup keeps no frames
    assert rep["setup"]["steps"][2]["step"] == {"fill": {"label": "Password", "value": "${LOGIN_PASSWORD}"}}


def test_secrets_come_from_the_environment_and_never_reach_the_report(tmp_path, monkeypatch):
    plan = copy_app(tmp_path)
    monkeypatch.delenv("LOGIN_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="not set: LOGIN_PASSWORD"):
        runner.run_plan(plan)

    monkeypatch.setenv("LOGIN_PASSWORD", PASSWORD)
    plan.write_text(
        plan.read_text()
        + """
  - id: leaks
    says: "Deliberately fails with the secret in its error message"
    steps:
      - goto: /account
      - expect_value: { label: Avatar, value: "${LOGIN_PASSWORD}" }
      - cmd: { run: "echo $LOGIN_PASSWORD" }
"""
    )
    run_dir, rep = runner.run_plan(plan, only=["leaks"])
    report.write(run_dir, rep)
    (claim,) = rep["claims"]
    assert claim["status"] == "FAIL" and "***" in claim["steps"][1]["error"]
    for f in ("report.json", "report.md"):
        assert PASSWORD not in (run_dir / f).read_text(), f


def test_failed_setup_fails_every_claim_but_fresh_ones_still_run(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGIN_PASSWORD", "wrong-password")
    run_dir, rep = runner.run_plan(copy_app(tmp_path))
    md = report.write(run_dir, rep)
    assert rep["setup"]["status"] == "FAIL"
    assert "## FAIL `_setup`" in md and "FAILED step 5 `expect_url /account`" in md
    status = {c["id"]: c for c in rep["claims"]}
    assert status["logged-out"]["status"] == "PASS"  # fresh: doesn't need the login
    assert status["signed-in"]["status"] == "FAIL"
    assert status["signed-in"]["reasons"][0].startswith("did not run, the setup failed: setup step 5 failed")
    frames = asyncio.run(server.get_frames(runner.SETUP_ID, plan_path=rep["plan"]))
    assert any(isinstance(x, str) and x.startswith("step 5 ") for x in frames)


def test_upload_of_a_missing_file_fails_the_step(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGIN_PASSWORD", PASSWORD)
    plan = copy_app(tmp_path)
    plan.write_text(plan.read_text().replace("fixtures/avatar.txt", "fixtures/nope.png"))
    _, rep = runner.run_plan(plan, only=["widgets"])
    (claim,) = rep["claims"]
    assert claim["status"] == "FAIL" and "no such file to upload: fixtures/nope.png" in claim["steps"][7]["error"]


def test_check_validates_the_new_options():
    def problems(**plan_keys):
        claim = {"id": "a", "says": "x", "steps": [{"cmd": {"run": "ls"}}]}
        claim.update(plan_keys.pop("claim", {}))
        return "\n".join(planfile.validate({**plan_keys, "claims": [claim]}))

    assert problems(setup=[{"cmd": {"run": "make seed"}}], before_each=[{"cmd": {"run": "make reset"}}]) == ""
    assert "'setup' must be a non-empty list" in problems(setup=[])
    assert "before_each step 1: unknown step 'teleport'" in problems(before_each=[{"teleport": "/"}])
    assert problems(viewport="mobile", claim={"viewport": {"width": 800, "height": 600}, "fresh": True}) == ""
    assert "'viewport' must be" in problems(viewport="tablet")
    assert "'viewport' must be" in problems(claim={"viewport": {"width": 10}})
    assert "'fresh' must be true or false" in problems(claim={"fresh": "yes"})
    steps = [{"upload": {"label": "Avatar"}}, {"expect_count": {"css": "li", "count": "3"}}]
    found = problems(base_url="http://x", claim={"steps": steps})
    assert "'upload' needs a 'file'" in found and "'expect_count' needs a 'count'" in found
    assert planfile.env_names({"steps": [{"fill": {"value": "${A} and ${B_2}"}}]}) == {"A", "B_2"}
    assert planfile.expand({"x": ["${A}-${NOT_SET}"]}, {"A": "1"}) == {"x": ["1-${NOT_SET}"]}


def test_report_json_has_no_session_state(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGIN_PASSWORD", PASSWORD)
    run_dir, rep = runner.run_plan(copy_app(tmp_path), only=["signed-in"])
    report.write(run_dir, rep)
    assert "session=ok" not in (run_dir / "report.json").read_text()
    assert "cookies" not in json.loads((run_dir / "report.json").read_text())["setup"]
