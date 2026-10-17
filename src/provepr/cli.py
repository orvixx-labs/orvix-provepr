import argparse
import sys
from pathlib import Path

from provepr import report, runner


def main() -> None:
    ap = argparse.ArgumentParser(prog="provepr", description="Check a change's claims in a real browser.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run .provepr/plan.yaml and write an evidence bundle")
    run.add_argument("--plan", type=Path, default=Path(".provepr/plan.yaml"))
    run.add_argument("--base-url", help="overrides base_url in the plan (or set PROVEPR_BASE_URL)")
    sub.add_parser("mcp", help="serve the MCP tools over stdio (for Claude Code / Codex)")
    args = ap.parse_args()

    if args.cmd == "mcp":
        from provepr.server import mcp

        mcp.run()
        return

    try:
        run_dir, rep = runner.run_plan(args.plan, args.base_url)
    except (OSError, ValueError, TimeoutError) as e:
        sys.exit(f"provepr: {e}")
    print(report.write(run_dir, rep))
    print(f"evidence: {run_dir}")
    sys.exit(1 if rep["counts"]["FAIL"] else 0)
