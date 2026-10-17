import asyncio
import shutil
from pathlib import Path

from provepr import report, runner, server

DEMO = Path(__file__).parents[1] / "examples" / "demo_app"


def run_demo(tmp_path: Path, fixed: bool) -> tuple[Path, dict]:
    app = tmp_path / "app"
    shutil.copytree(DEMO, app, ignore=shutil.ignore_patterns("runs"))
    if fixed:
        src = app / "server.py"
        src.write_text(src.read_text().replace('data["nme"]', 'data["name"]'))
    run_dir, rep = runner.run_plan(app / ".provepr" / "plan.yaml")
    report.write(run_dir, rep)
    return run_dir, rep


def test_catches_planted_bug(tmp_path):
    run_dir, rep = run_demo(tmp_path, fixed=False)
    status = {c["id"]: c for c in rep["claims"]}
    assert status["page-loads"]["status"] == "PASS"
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
    _, rep = run_demo(tmp_path, fixed=True)
    assert rep["counts"] == {"PASS": 2, "FAIL": 0, "UNVERIFIED": 1}
