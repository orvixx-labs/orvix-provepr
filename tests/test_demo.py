import asyncio
import shutil
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from provepr import report, runner, server

DEMO = Path(__file__).parents[1] / "examples" / "demo_app"


def copy_demo(tmp_path: Path, fixed: bool) -> Path:
    app = tmp_path / "app"
    shutil.copytree(DEMO, app, ignore=shutil.ignore_patterns("runs"))
    if fixed:
        src = app / "server.py"
        src.write_text(src.read_text().replace('data["nme"]', 'data["name"]'))
    return app / ".provepr" / "plan.yaml"


def test_catches_planted_bug(tmp_path):
    run_dir, rep = runner.run_plan(copy_demo(tmp_path, fixed=False))
    report.write(run_dir, rep)
    status = {c["id"]: c for c in rep["claims"]}
    assert status["page-loads"]["status"] == "PASS"
    assert status["profile-api"]["status"] == "PASS"
    assert "HTTP 200" in status["profile-api"]["steps"][0]["note"]
    assert status["name-length-limit"]["status"] == "UNVERIFIED"
    bad = status["save-persists"]
    assert bad["status"] == "FAIL"
    assert any("HTTP 500 POST" in r for r in bad["reasons"])
    assert [s["ok"] for s in bad["steps"]] == [True] * 5 + [False]  # UI said "Saved" anyway
    # The 500 already failed the claim, so step 6 stops waiting instead of using its full 5 s timeout.
    assert "stopped waiting, the claim already failed (HTTP 500" in bad["steps"][5]["error"]
    assert "Actual value" in bad["steps"][5]["error"] and bad["steps"][5]["ms"] < 2000
    for f in ("report.json", "report.md", "contact.png"):
        assert (run_dir / f).exists(), f
    assert len((run_dir / "report.md").read_text()) < 4000  # stays cheap for the agent

    frames = asyncio.run(server.get_frames("save-persists", plan_path=rep["plan"]))
    assert [x for x in frames if isinstance(x, str) and x.startswith("step")][-1].startswith("step 6 ")


def test_fixed_app_passes(tmp_path):
    _, rep = runner.run_plan(copy_demo(tmp_path, fixed=True))
    assert rep["counts"] == {"PASS": 3, "FAIL": 0, "UNVERIFIED": 1}


def test_http_step_and_only(tmp_path):
    plan = copy_demo(tmp_path, fixed=False)
    plan.write_text(
        plan.read_text()
        + """
  - id: bad-input-rejected
    says: "A valid save is answered with 400 (deliberately wrong, to see the step fail)"
    steps:
      - http: { method: POST, path: /api/profile, json: { name: x }, expect_status: 400 }
"""
    )
    _, rep = runner.run_plan(plan, only=["bad-input-rejected"])
    (claim,) = rep["claims"]
    assert claim["status"] == "FAIL"  # the demo answers 500, not 400
    assert "expected HTTP 400" in claim["steps"][0]["error"] and "HTTP 500" in claim["steps"][0]["error"]


def test_mcp_errors_reach_the_agent(tmp_path):
    plan = copy_demo(tmp_path, fixed=False)
    with pytest.raises(ToolError, match="no such claim"):
        asyncio.run(server.run_proof(plan_path=str(plan), claims=["nope"]))
    plan.write_text("claims:\n  - id: x\n    says: y\n    steps: [{ teleport: / }]\n")
    with pytest.raises(ToolError, match="unknown step 'teleport'"):
        asyncio.run(server.run_proof(plan_path=str(plan)))


def test_evidence_kept_only_where_the_agent_needs_it(tmp_path):
    plan = copy_demo(tmp_path, fixed=False)
    runs = plan.parent / "runs"
    for i in range(6):  # five pretend older runs
        (runs / f"20000101-00000{i}-000000").mkdir(parents=True)
    run_dir, rep = runner.run_plan(plan)
    report.write(run_dir, rep)

    assert len(list(runs.iterdir())) == runner.KEEP_RUNS and run_dir.exists()
    assert not (run_dir / "videos").exists()  # no video unless asked for
    assert not (run_dir / "frames" / "page-loads").exists()  # passed: no frames
    kept = sorted(p.name for p in (run_dir / "frames" / "save-persists").iterdir())
    assert kept == ["05.png", "06.png"]  # failed at step 6: that step and the one before
    assert (run_dir / "contact.png").exists()
    msg = asyncio.run(server.get_frames("page-loads", plan_path=rep["plan"]))
    assert "only failing and changed steps keep their frames" in msg[0]
    msg = asyncio.run(server.get_frames("save-persists", steps=[1], plan_path=rep["plan"]))
    assert "kept: steps [5, 6]" in msg[0]

    run_dir, rep = runner.run_plan(plan, only=["page-loads"], video=True)
    report.write(run_dir, rep, keep_evidence=True)
    assert (run_dir / "videos" / "page-loads.webm").exists()
    assert len(list((run_dir / "frames" / "page-loads").iterdir())) == 4


def add_claims(plan: Path, yaml_text: str) -> Path:
    plan.write_text(plan.read_text() + yaml_text)
    return plan


def test_mock_reaches_error_states_without_failing_on_the_fake(tmp_path):
    plan = add_claims(
        copy_demo(tmp_path, fixed=True),
        """
  - id: save-error-shown
    says: "When saving fails, the page does not say Saved"
    steps:
      - goto: /
      - mock: { path: /api/profile, method: POST, status: 500, json: { error: down } }
      - fill: { label: Name, value: Ada }
      - click: { role: button, name: Save }
      - expect_no_text: Saved
  - id: slow-save
    says: "A slow save still finishes"
    steps:
      - goto: /
      - mock: { path: /api/profile, method: POST, delay: 1500 }
      - click: { role: button, name: Save }
      - expect_no_text: Saved
      - expect_text: Saved
""",
    )
    _, rep = runner.run_plan(plan, only=["save-error-shown", "slow-save"])
    err, slow = rep["claims"]
    # The demo UI says "Saved" even when the save fails: the step catches it, the faked 500 does not.
    assert err["status"] == "FAIL" and [s["ok"] for s in err["steps"]] == [True] * 4 + [False]
    assert not any("HTTP 500" in r for r in err["reasons"]) and err["network_errors"] == []
    assert "mock POST /api/profile: answer" in err["steps"][1]["note"]
    assert slow["status"] == "PASS"  # "Saved" was absent while held, then appeared


def test_cmd_steps_check_cli_output_without_a_browser(tmp_path):
    (tmp_path / ".provepr").mkdir()
    plan = tmp_path / ".provepr" / "plan.yaml"
    plan.write_text(
        """
claims:
  - id: greets
    says: "The CLI greets by name"
    steps:
      - cmd: { run: "read n; echo hello $n", stdin: Ada, expect_output: hello Ada }
  - id: bad-flag
    says: "An unknown flag exits with code 2"
    steps:
      - cmd: { run: "echo 'usage: tool' >&2; exit 1", expect_exit: 2 }
  - id: hangs
    says: "Finishes quickly"
    steps:
      - cmd: { run: "sleep 10", timeout: 1 }
"""
    )
    run_dir, rep = runner.run_plan(plan)
    report.write(run_dir, rep)
    greets, bad, hangs = rep["claims"]
    assert greets["status"] == "PASS" and "[exit 0]" in greets["steps"][0]["output"]
    assert bad["status"] == "FAIL" and "expected exit 2, got 1; last output: usage: tool" in bad["steps"][0]["error"]
    assert hangs["status"] == "FAIL" and "timed out after 1s" in hangs["steps"][0]["error"]
    assert hangs["steps"][0]["ms"] < 5000
    assert (run_dir / "frames" / "bad-flag" / "01.png").exists()  # the terminal view, kept because it failed
    assert not (run_dir / "videos").exists()  # no browser was started


def test_screens_compared_with_baseline(tmp_path):
    plan = copy_demo(tmp_path, fixed=True)
    run_dir, rep = runner.run_plan(plan, only=["page-loads"])
    md = report.write(run_dir, rep, compare=True, save=True)
    assert "No baseline" in md and (plan.parent / "baseline" / "page-loads" / "04.png").exists()

    run_dir, rep = runner.run_plan(plan, only=["page-loads"])
    report.write(run_dir, rep, compare=True)
    assert rep["baseline"]["run"] and not any(s.get("visual_change") for s in rep["claims"][0]["steps"])

    page = plan.parent.parent / "index.html"
    page.write_text(page.read_text().replace("<h1>Profile</h1>", "<h1>Profile settings</h1>"))
    run_dir, rep = runner.run_plan(plan, only=["page-loads"])
    md = report.write(run_dir, rep, compare=True)
    claim = rep["claims"][0]
    assert claim["status"] == "PASS"  # a visual change is reported, not judged
    change = claim["steps"][-1]["visual_change"]
    assert change["percent"] > 0 and (run_dir / change["diff"]).exists()
    assert "looks different from the baseline at step 1, 2, 3, 4: up to" in md
    frames = asyncio.run(server.get_frames("page-loads", plan_path=rep["plan"]))
    assert any(isinstance(x, str) and "diff from the baseline" in x for x in frames)


def test_parallel_claims_report_like_sequential(tmp_path):
    plan = copy_demo(tmp_path, fixed=False)
    plan.write_text(plan.read_text() + "\nparallel: 3\n")
    _, rep = runner.run_plan(plan)
    assert [c["id"] for c in rep["claims"]] == ["page-loads", "save-persists", "profile-api", "name-length-limit"]
    assert [c["status"] for c in rep["claims"]] == ["PASS", "FAIL", "PASS", "UNVERIFIED"]
    assert "--only save-persists" in rep["claims"][1]["note"]  # a FAIL is reported as is, with a hint
