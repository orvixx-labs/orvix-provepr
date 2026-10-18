import subprocess
import sys

import yaml

from provepr import plan
from provepr.init import BEGIN, END, init


def test_check_lists_every_problem():
    bad = {
        "claims": [
            {"id": "../escape", "says": "x", "steps": [{"goto": "/"}, {"expect_text": "hi"}]},
            {"id": "dup", "says": "x", "steps": [{"click": {"role": "button"}}]},
            {"id": "dup", "steps": [{"teleport": "/"}, {"fill": {"label": "Name"}}, {"http": {"method": "GET"}}]},
        ]
    }
    problems = "\n".join(plan.validate(bad))
    assert "missing 'base_url'" in problems
    assert "claim '../escape': 'id' must be" in problems
    assert "claim 'dup': has steps but no expect_* or http step" in problems
    assert "claim 'dup': duplicate id" in problems
    assert "missing 'says'" in problems
    assert "unknown step 'teleport'" in problems
    assert "'fill' needs a 'value'" in problems
    assert "'http' needs at least a 'path'" in problems


def test_init_is_idempotent_and_keeps_user_content(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# My project\n\nUse tabs.\n")
    (tmp_path / "CLAUDE.md").write_text("Be brief.\n")
    first = init(tmp_path)
    assert all(line.split()[0] in ("created", "updated") for line in first), first
    second = init(tmp_path)
    assert all(line.startswith("unchanged") for line in second), second

    agents = (tmp_path / "AGENTS.md").read_text()
    assert agents.startswith("# My project") and agents.count(BEGIN) == 1 and agents.count(END) == 1
    assert (tmp_path / "CLAUDE.md").read_text() == "Be brief.\n\n@AGENTS.md\n"
    assert (tmp_path / ".gitignore").read_text() == ".provepr/runs/\n"
    assert plan.validate(yaml.safe_load((tmp_path / ".provepr/plan.yaml").read_text())) == []
    workflow = yaml.safe_load((tmp_path / ".github/workflows/provepr.yml").read_text())
    steps = [s.get("run", "") for s in workflow["jobs"]["provepr"]["steps"]]
    assert any("provepr check" in s for s in steps) and any("provepr run" in s for s in steps)


def test_docs_command_prints_bundled_guide():
    out = subprocess.run([sys.executable, "-m", "provepr.cli", "docs"], capture_output=True, text=True, check=True)
    assert "Never weaken the plan" in out.stdout and "`http`" in out.stdout
