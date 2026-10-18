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
    for f in ("report.json", "report.md", "contact.png", "videos/save-persists.webm"):
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


def test_evidence_kept_only_for_failures_and_recent_runs(tmp_path):
    plan = copy_demo(tmp_path, fixed=False)
    runs = plan.parent / "runs"
    for i in range(6):  # five pretend older runs
        (runs / f"20000101-00000{i}-000000").mkdir(parents=True)
    run_dir, rep = runner.run_plan(plan)
    report.write(run_dir, rep)

    assert len(list(runs.iterdir())) == runner.KEEP_RUNS and run_dir.exists()
    assert not (run_dir / "frames" / "page-loads").exists()  # passed: deleted
    assert not (run_dir / "videos" / "page-loads.webm").exists()
    assert (run_dir / "frames" / "save-persists").exists()  # failed: kept
    assert (run_dir / "videos" / "save-persists.webm").exists()
    assert (run_dir / "contact.png").exists()
    msg = asyncio.run(server.get_frames("page-loads", plan_path=rep["plan"]))
    assert "deleted after passing" in msg[0]
