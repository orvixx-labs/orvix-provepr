"""Benchmark: the same planted-bug task through provepr, Playwright MCP and Claude in Chrome.

Each run gets a fresh copy of examples/demo_app (two planted bugs: the server reads the wrong JSON key,
and the page says "Saved" even when saving fails) and the same prompt, naming only that arm's tool.
Claude Code runs headless with the user's settings, plugins and MCP servers switched off, so each arm
sees only its own tool. Afterwards a hidden provepr plan grades the code the agent left behind, and the
same session is asked to verify again (the rerun cost).

    uv run python bench/run.py --arms provepr,playwright,chrome --reps 2 --model sonnet

Needs `claude` logged in, and for the playwright arm the MCP server from npm (see --pw-mcp).
The chrome arm drives your real Chrome through the Claude in Chrome extension.
Writes bench/results.jsonl (one line per run) and prints a markdown table.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "examples" / "demo_app"
PORT = 8765
RUN_TIMEOUT_S = 1200

PROMPT = (
    "I built a profile page where you can type your name and save it. Start it with `python3 server.py`; "
    f"it serves http://127.0.0.1:{PORT}/. Before I open a PR, prove the feature works in a real browser and "
    "fix anything that's broken. Use {tool} for the browser checks."
)
RERUN_PROMPT = "Verify again, the same way, that the feature works now. Report the result in one line."

ARMS = {
    "provepr": ("provepr", "mcp__provepr"),
    "playwright": ("the Playwright browser tools", "mcp__playwright"),
    "chrome": ("Claude in Chrome", "mcp__claude-in-chrome"),
}

# Graded on the code the agent left, with checks the agent never saw.
GRADER = """\
base_url: http://127.0.0.1:{port}
start: python3 {app}/server.py
claims:
  - id: save-persists
    says: "Bug 1 fixed: a saved name is still there after a reload"
    steps:
      - goto: /
      - fill: {{ label: Name, value: Ada Lovelace }}
      - click: {{ role: button, name: Save }}
      - expect_text: Saved
      - reload: true
      - expect_value: {{ label: Name, value: Ada Lovelace }}
  - id: failure-not-saved
    says: "Bug 2 fixed: when the server rejects the save, the page does not say Saved"
    steps:
      - goto: /
      - mock: {{ path: /api/profile, method: POST, status: 500, json: {{ error: down }} }}
      - fill: {{ label: Name, value: Ada }}
      - click: {{ role: button, name: Save }}
      - wait: 1000
      - expect_no_text: Saved
"""


def free_port() -> None:
    """Kill whatever an agent left listening on the demo port, so the next run starts clean."""
    out = subprocess.run(["ss", "-ltnpH", f"sport = :{PORT}"], capture_output=True, text=True, check=False).stdout
    for pid in {int(p) for p in re.findall(r"pid=(\d+)", out)}:
        os.kill(pid, signal.SIGTERM)
    time.sleep(0.5)


def fresh_app(work: Path) -> Path:
    app = work / "app"
    shutil.copytree(DEMO, app, ignore=shutil.ignore_patterns(".provepr", "__pycache__"))
    src = app / "server.py"
    src.write_text(
        src.read_text().replace(
            "Tiny profile app used to demo and test provepr. Contains a planted bug.", "Profile app."
        )
    )
    return app


def claude_flags(arm: str, work: Path, args) -> list[str]:
    mcp = {"mcpServers": {}}
    flags = ["--no-chrome"]
    if arm == "provepr":
        mcp["mcpServers"]["provepr"] = {"command": "uv", "args": ["run", "--project", str(REPO), "provepr", "mcp"]}
        flags = ["--no-chrome", "--plugin-dir", str(REPO)]  # the plugin brings the skill
    elif arm == "playwright":
        mcp["mcpServers"]["playwright"] = {
            "command": "node",
            "args": [args.pw_mcp, "--headless", "--browser", "chromium", "--isolated"],
        }
    elif arm == "chrome":
        flags = ["--chrome"]
    (work / "mcp.json").write_text(json.dumps(mcp))
    tools = ["Bash", "Read", "Edit", "Write", "Glob", "Grep", "Skill", ARMS[arm][1]]
    return [
        *flags,
        "--setting-sources", "project",
        "--strict-mcp-config", "--mcp-config", str(work / "mcp.json"),
        "--allowedTools", *tools,
        "--model", args.model,
        "--max-budget-usd", str(args.max_usd),
        "--output-format", "json",
    ]  # fmt: skip


def claude(prompt: str, cwd: Path, flags: list[str]) -> dict:
    start = time.monotonic()
    try:
        p = subprocess.run(
            ["claude", "-p", prompt, *flags],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_S,
            check=False,
        )
        out = json.loads(p.stdout)
    except subprocess.TimeoutExpired:
        out = {"is_error": True, "result": f"timed out after {RUN_TIMEOUT_S}s"}
    except json.JSONDecodeError:
        out = {"is_error": True, "result": (p.stderr or p.stdout)[-500:]}
    usage = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    for m in (out.get("modelUsage") or {}).values():  # every model the session used, subagents included
        usage["input"] += m.get("inputTokens", 0)
        usage["cache_write"] += m.get("cacheCreationInputTokens", 0)
        usage["cache_read"] += m.get("cacheReadInputTokens", 0)
        usage["output"] += m.get("outputTokens", 0)
    return {
        "session": out.get("session_id"),
        "ok": not out.get("is_error"),
        "result": str(out.get("result", ""))[-600:],
        "turns": out.get("num_turns"),
        "seconds": round(time.monotonic() - start),
        "cost_usd": round(out.get("total_cost_usd") or 0, 3),
        "tokens": usage,
        "tokens_total": sum(usage.values()),
    }


def grade(app: Path, work: Path) -> dict:
    plan = work / "grader" / ".provepr" / "plan.yaml"
    plan.parent.mkdir(parents=True)
    plan.write_text(GRADER.format(port=PORT, app=app))
    p = subprocess.run(
        ["uv", "run", "--project", str(REPO), "provepr", "run", "--plan", str(plan)],
        capture_output=True,
        text=True,
        check=False,
    )
    status = {cid: st for st, cid in re.findall(r"^## (PASS|FAIL|UNVERIFIED) `([^`]+)`", p.stdout, re.MULTILINE)}
    if not status:  # the app didn't start, e.g. the agent broke server.py
        return {"bug1_fixed": False, "bug2_fixed": False, "grader_error": (p.stdout + p.stderr)[-500:]}
    return {
        "bug1_fixed": status.get("save-persists") == "PASS",
        "bug2_fixed": status.get("failure-not-saved") == "PASS",
    }


def run_once(arm: str, rep: int, args) -> dict:
    work = Path(tempfile.mkdtemp(prefix=f"provepr-bench-{arm}-"))
    app = fresh_app(work)
    flags = claude_flags(arm, work, args)
    free_port()
    first = claude(PROMPT.format(tool=ARMS[arm][0]), app, flags)
    free_port()
    graded = grade(app, work)
    free_port()
    rerun = claude(RERUN_PROMPT, app, [*flags, "--resume", first["session"]]) if first["session"] else None
    free_port()
    return {
        "arm": arm,
        "rep": rep,
        "model": args.model,
        "work": str(work),
        "task": first,
        "graded": graded,
        "rerun": rerun,
    }


def table(rows: list[dict]) -> str:
    lines = [
        "| Arm | Run | Bug 1 (save) | Bug 2 (false 'Saved') | Tokens | Cost | Time | Turns | Rerun tokens | Rerun cost | Rerun time |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        t, g, rr = r["task"], r["graded"], r["rerun"] or {}
        mark = lambda b: "fixed" if b else "missed"
        lines.append(
            f"| {r['arm']} | {r['rep']} | {mark(g.get('bug1_fixed'))} | {mark(g.get('bug2_fixed'))} | "
            f"{t['tokens_total']:,} | ${t['cost_usd']} | {t['seconds']}s | {t['turns']} | "
            f"{rr.get('tokens_total', 0):,} | ${rr.get('cost_usd', 0)} | {rr.get('seconds', 0)}s |"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arms", default="provepr,playwright,chrome")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--max-usd", type=float, default=5.0, help="budget cap per claude call")
    ap.add_argument("--pw-mcp", default=os.environ.get("PW_MCP_CLI", ""), help="path to @playwright/mcp's cli.js")
    ap.add_argument("--out", type=Path, default=REPO / "bench" / "results.jsonl")
    args = ap.parse_args()
    arms = args.arms.split(",")
    if "playwright" in arms and not Path(args.pw_mcp).is_file():
        sys.exit("--pw-mcp must point to @playwright/mcp's cli.js (npm install @playwright/mcp@0.0.83)")
    rows = []
    for rep in range(1, args.reps + 1):
        for arm in arms:  # interleaved, so drift over time hits every arm alike
            print(f"running {arm} #{rep} ...", file=sys.stderr, flush=True)
            row = run_once(arm, rep, args)
            rows.append(row)
            with args.out.open("a") as f:
                f.write(json.dumps(row) + "\n")
    print(table(rows))


if __name__ == "__main__":
    main()
