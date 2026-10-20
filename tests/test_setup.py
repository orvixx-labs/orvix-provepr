import subprocess
import sys

import pytest
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
    assert "claim 'dup': has steps but no expect_*, http or cmd step" in problems
    assert "claim 'dup': duplicate id" in problems
    assert "missing 'says'" in problems
    assert "unknown step 'teleport'" in problems
    assert "'fill' needs a 'value'" in problems
    assert "'http' needs at least a 'path'" in problems


def test_check_new_steps():
    def problems(*steps, **plan_keys):
        return "\n".join(plan.validate({**plan_keys, "claims": [{"id": "a", "says": "x", "steps": list(steps)}]}))

    assert problems({"cmd": {"run": "ls"}}) == ""  # cmd-only plans need no base_url, and cmd is a check
    assert "'cmd' needs at least a 'run'" in problems({"cmd": {"expect_exit": 0}})
    assert "'cmd' has unknown keys ['cwd']" in problems({"cmd": {"run": "ls", "cwd": "/"}})
    assert "'cmd' timeout must be a number" in problems({"cmd": {"run": "ls", "timeout": "1m"}})
    ok = {"base_url": "http://x"}
    assert problems({"mock": {"path": "/api", "status": 500}}, {"expect_text": "Error"}, **ok) == ""
    assert "needs 'status', 'body', 'json', 'fail: true' or 'delay'" in problems({"mock": {"path": "/a"}}, **ok)
    assert "not both" in problems({"mock": {"path": "/a", "fail": True, "status": 500}}, **ok)
    assert "proves nothing" in problems({"mock": {"path": "/a", "delay": 9}}, {"goto": "/"}, **ok)
    assert "'waivers' must map" in problems({"cmd": {"run": "ls"}}, waivers={"a": ""})
    assert "'parallel' must be a whole number" in problems({"cmd": {"run": "ls"}}, parallel=0)


def test_check_base_flags_removed_and_changed_checks(tmp_path):
    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=tmp_path, check=True)

    path = tmp_path / ".provepr" / "plan.yaml"
    path.parent.mkdir()
    old = {
        "base_url": "http://x",
        "claims": [
            {"id": "keep", "says": "x", "steps": [{"goto": "/"}, {"expect_text": "Hi"}]},
            {"id": "edit", "says": "x", "steps": [{"goto": "/"}, {"expect_text": "Saved"}, {"expect_url": "/ok"}]},
            {"id": "gone", "says": "x", "steps": [{"goto": "/"}, {"expect_text": "Bye"}]},
        ],
    }
    path.write_text(yaml.safe_dump(old))
    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-qm", "plan")

    new = yaml.safe_load(path.read_text())
    new["claims"] = new["claims"][:2]
    new["claims"][1]["steps"][1] = {"expect_text": "Sav"}  # loosened
    new["claims"].append({"id": "added", "says": "x", "steps": [{"goto": "/"}, {"expect_text": "New"}]})
    base = plan.base_plan(path, "main")
    assert plan.weakened(new, base) == [
        "claim 'edit': check removed or changed: `{expect_text: Saved}`",
        "claim 'gone' was removed",
    ]
    new["waivers"] = {"gone": "the goodbye page was removed on purpose"}
    assert plan.weakened(new, base) == ["claim 'edit': check removed or changed: `{expect_text: Saved}`"]

    base["waivers"] = new["waivers"]  # a waiver already on the base branch does not excuse new removals
    assert "claim 'gone' was removed" in plan.weakened(new, base)
    assert plan.weakened(old, base) == []

    path.write_text(yaml.safe_dump(new))
    out = subprocess.run(
        [sys.executable, "-m", "provepr.cli", "check", "--plan", str(path), "--base", "main"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 1 and "check removed or changed" in out.stderr
    with pytest.raises(ValueError, match="not a commit"):
        plan.base_plan(path, "no-such-branch")


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
    assert (tmp_path / ".gitignore").read_text() == ".provepr/runs/\n.provepr/baseline/\n"
    assert plan.validate(yaml.safe_load((tmp_path / ".provepr/plan.yaml").read_text())) == []
    workflow = yaml.safe_load((tmp_path / ".github/workflows/provepr.yml").read_text())
    steps = [s.get("run", "") for s in workflow["jobs"]["provepr"]["steps"]]
    assert any("provepr check --base" in s for s in steps) and any("provepr run" in s for s in steps)


def test_docs_command_prints_bundled_guide():
    out = subprocess.run([sys.executable, "-m", "provepr.cli", "docs"], capture_output=True, text=True, check=True)
    assert "Never weaken the plan" in out.stdout and "`http`" in out.stdout
