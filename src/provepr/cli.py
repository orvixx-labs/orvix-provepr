import argparse
import sys
from importlib.resources import files
from pathlib import Path

from provepr import plan, report, runner
from provepr.init import init


def guide() -> str:
    return (files("provepr") / "docs" / "guide.md").read_text()


def main() -> None:
    ap = argparse.ArgumentParser(prog="provepr", description="Make coding agents prove their changes work.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run the plan's claims and write an evidence bundle")
    run.add_argument("--plan", type=Path, default=Path(".provepr/plan.yaml"))
    run.add_argument("--base-url", help="overrides base_url in the plan (or set PROVEPR_BASE_URL)")
    run.add_argument("--only", help="comma-separated claim ids to run (default: all)")
    run.add_argument("--keep-evidence", action="store_true", help="keep screenshots and video of passing claims too")
    run.add_argument("--save-baseline", action="store_true", help="save this run's screens as the visual baseline")
    run.add_argument("--compare", action="store_true", help="report screens that look different from the baseline")

    check = sub.add_parser("check", help="validate the plan without running it")
    check.add_argument("--plan", type=Path, default=Path(".provepr/plan.yaml"))
    check.add_argument("--base", metavar="REF", help="also fail if claims or checks on this git ref were removed")

    ini = sub.add_parser("init", help="set up AGENTS.md, CLAUDE.md, CI workflow, starter plan, .gitignore")
    ini.add_argument("dir", nargs="?", type=Path, default=Path("."))
    ini.add_argument("--no-ci", action="store_true", help="skip .github/workflows/provepr.yml")

    sub.add_parser("docs", help="print the guide for coding agents (matches this version)")
    sub.add_parser("mcp", help="serve the MCP tools over stdio (for Claude Code / Codex)")
    args = ap.parse_args()

    match args.cmd:
        case "docs":
            print(guide())
        case "init":
            print("\n".join(init(args.dir, ci=not args.no_ci)))
            print("\nNext: edit base_url/start in .provepr/plan.yaml, then ask your agent to add claims.")
        case "check":
            try:
                p = plan.load(args.plan)
                old = plan.base_plan(args.plan, args.base) if args.base else None
            except (OSError, ValueError) as e:
                sys.exit(f"provepr: {e}")
            if args.base and old is None:
                print(f"{args.base} has no plan at {args.plan}; nothing to compare")
            elif args.base and (lost := plan.weakened(p, old)):
                sys.exit(
                    f"provepr: the plan is weaker than on {args.base}:\n"
                    + "\n".join(f"- {x}" for x in lost)
                    + "\nRestore them, or if the feature was removed on purpose, add `waivers: {<claim-id>: <why>}`"
                    " to the plan so the reason shows in the PR."
                )
            print(
                f"ok: {len(p['claims'])} claim(s) in {args.plan}"
                + (f", none weakened since {args.base}" if old else "")
            )
        case "mcp":
            from provepr.server import mcp

            mcp.run()
        case "run":
            only = [c.strip() for c in args.only.split(",") if c.strip()] if args.only else None
            try:
                run_dir, rep = runner.run_plan(args.plan, args.base_url, only)
            except (OSError, ValueError, TimeoutError) as e:
                sys.exit(f"provepr: {e}")
            print(report.write(run_dir, rep, args.keep_evidence, args.compare, args.save_baseline))
            print(f"evidence: {run_dir}")
            sys.exit(1 if rep["counts"]["FAIL"] else 0)


if __name__ == "__main__":
    main()
