"""`provepr init`: set a repo up so any coding agent and CI use provepr the same way.

Everything here is a fixed template: identical for every project, so a command writes it
instead of letting each agent improvise. Safe to run again: existing files are kept, and only
the marked AGENTS.md block is refreshed.
"""

from importlib.metadata import version
from pathlib import Path

BEGIN, END = "<!-- BEGIN:provepr-agent-rules -->", "<!-- END:provepr-agent-rules -->"

AGENTS_BLOCK = f"""{BEGIN}
## provepr: prove UI changes before calling them done

This project uses provepr. Before you say a change a user can see or trigger is done, or open a PR for it:

1. Read the guide for the installed version: run `provepr docs` (or call the provepr `get_guide` MCP tool).
   It is the source of truth for the plan format and rules.
2. Add claims for your change to `.provepr/plan.yaml` BEFORE running. Keep the existing claims.
3. Run `provepr check`, then `provepr run` (or the `run_proof` tool). Fix the code until no claim FAILs.
4. Never delete claims or loosen expectations to make a run pass. Report FAIL and UNVERIFIED claims honestly.

This block is managed by `provepr init`; edit outside the markers.
{END}"""

STARTER_PLAN = """\
# Claims this project's changes make, checked by provepr in a real browser. Guide: `provepr docs`.
# Add claims for every change and keep the old ones: CI runs them all.
base_url: http://localhost:3000
start: npm run dev    # starts the app from the project root; CI needs this. Change both lines to fit.

claims:
  - id: home-loads
    says: "The home page loads without errors"
    steps:
      - goto: /
      - expect_visible: { css: body }
"""

WORKFLOW = """\
# Written by `provepr init`. Runs every claim in .provepr/plan.yaml on each pull request.
name: provepr
on:
  pull_request:

jobs:
  provepr:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6

      # Install what the plan's `start:` command needs, for example:
      # - uses: actions/setup-node@v4
      #   with: { node-version: 22, cache: npm }
      # - run: npm ci

      - name: Install Chromium
        run: uvx playwright@PLAYWRIGHT_VERSION install --with-deps chromium
      - name: Check the plan
        run: uvx --from "INSTALL_SPEC" provepr check
      - name: Run the claims
        run: uvx --from "INSTALL_SPEC" provepr run
      - name: Upload evidence
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: provepr-evidence
          path: .provepr/runs/
          if-no-files-found: ignore
"""


def upsert_block(text: str) -> str:
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        return head + AGENTS_BLOCK + rest.split(END, 1)[1]
    return (text.rstrip() + "\n\n" if text.strip() else "") + AGENTS_BLOCK + "\n"


def init(root: Path, ci: bool = True) -> list[str]:
    """Set up `root`. Returns one line per file saying what happened."""
    done = []

    def write(rel: str, new: str, old: str | None) -> None:
        path = root / rel
        if old == new:
            done.append(f"unchanged  {rel}")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new)
        done.append(f"{'created' if old is None else 'updated':<10} {rel}")

    def read(rel: str) -> str | None:
        path = root / rel
        return path.read_text() if path.exists() else None

    agents = read("AGENTS.md")
    write("AGENTS.md", upsert_block(agents or ""), agents)

    claude = read("CLAUDE.md")
    if claude is None:
        write("CLAUDE.md", "@AGENTS.md\n", None)
    elif "@AGENTS.md" not in claude:
        write("CLAUDE.md", claude.rstrip() + "\n\n@AGENTS.md\n", claude)
    else:
        done.append("unchanged  CLAUDE.md")

    plan = read(".provepr/plan.yaml")
    write(".provepr/plan.yaml", plan if plan is not None else STARTER_PLAN, plan)

    gitignore = read(".gitignore")
    if gitignore is None or ".provepr/runs/" not in gitignore.splitlines():
        base = (gitignore or "").rstrip()
        write(".gitignore", (base + "\n" if base else "") + ".provepr/runs/\n", gitignore)
    else:
        done.append("unchanged  .gitignore")

    if ci:
        wf = read(".github/workflows/provepr.yml")
        # CI pins the same provepr (and so the same guide and plan format) that wrote this file.
        install = f"orvix-provepr=={version('orvix-provepr')}"
        new = WORKFLOW.replace("PLAYWRIGHT_VERSION", version("playwright")).replace("INSTALL_SPEC", install)
        write(".github/workflows/provepr.yml", wf if wf is not None else new, wf)  # never clobber edits
    return done
