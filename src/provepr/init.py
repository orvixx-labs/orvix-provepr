"""`provepr init`: set a repo up so any coding agent and CI use provepr the same way.

Everything here is a fixed template: identical for every project, so a command writes it
instead of letting each agent improvise. Safe to run again: existing files are kept, except the
marked AGENTS.md block and a CI workflow nobody edited, which are brought up to this version.
"""

import re
from importlib.metadata import version
from pathlib import Path

BEGIN, END = "<!-- BEGIN:provepr-agent-rules -->", "<!-- END:provepr-agent-rules -->"
IGNORED = (".provepr/runs/", ".provepr/baseline/")  # evidence and visual baseline: local output, never committed

AGENTS_BLOCK = f"""{BEGIN}
## provepr: prove changes before calling them done

This project uses provepr. Before you say a change is done, or open a PR for it (UI, API, or command-line
tool: provepr checks all three):

1. Read the guide for the installed version: run `provepr docs` (or call the provepr `get_guide` MCP tool).
   It is the source of truth for the plan format and rules.
2. Add claims for your change to `.provepr/plan.yaml` BEFORE running. Keep the existing claims.
3. Run `provepr check --base <base branch>`, then `provepr run` (or the `run_proof` tool). Fix the code until no claim FAILs.
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
      - name: Fetch the base branch (provepr check --base compares the plan with it)
        run: git fetch --depth=1 origin "+refs/heads/$GITHUB_BASE_REF:refs/remotes/origin/$GITHUB_BASE_REF"
      - uses: astral-sh/setup-uv@v6

      # Install what the plan's `start:` command needs, for example:
      # - uses: actions/setup-node@v4
      #   with: { node-version: 22, cache: npm }
      # - run: npm ci

      - name: Install Chromium
        run: uvx playwright@PLAYWRIGHT_VERSION install --with-deps chromium
      - name: Check the plan (and that no claim was removed or weakened)
        run: uvx --from "INSTALL_SPEC" provepr check --base "origin/$GITHUB_BASE_REF"
      - name: Run the claims
        run: uvx --from "INSTALL_SPEC" provepr run
        # If the plan reads ${NAME} values (e.g. a test login), add them as repository secrets and pass them:
        # env:
        #   LOGIN_PASSWORD: ${{ secrets.LOGIN_PASSWORD }}
      - name: Upload evidence
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: provepr-evidence
          path: .provepr/runs/
          if-no-files-found: ignore
"""


# Earlier versions' workflow templates: a file still matching one (apart from version pins) was never
# edited, so `init` may replace it. Add the old template here whenever WORKFLOW changes.
FETCH_STEP = WORKFLOW[WORKFLOW.index("      - name: Fetch") : WORKFLOW.index("      - uses: astral-sh")]
SECRETS_HINT = WORKFLOW[WORKFLOW.index("        # If the plan reads") : WORKFLOW.index("      - name: Upload")]
V02 = WORKFLOW.replace(SECRETS_HINT, "")
OLD_WORKFLOWS = (
    V02,  # 0.2.x
    V02.replace(FETCH_STEP, "").replace(  # 0.1.x
        'Check the plan (and that no claim was removed or weakened)\n        run: uvx --from "INSTALL_SPEC" provepr check --base "origin/$GITHUB_BASE_REF"',
        'Check the plan\n        run: uvx --from "INSTALL_SPEC" provepr check',
    ),
)


PIN = re.compile(r"orvix-provepr==([^\"\s]+)")
EDITED = (
    "provepr.yml was edited, so `provepr init` keeps it, but it pins an older provepr: update the pin and"
    " compare it with the template (delete it and rerun `provepr init` to see it)."
)


def pinned() -> str:
    # CI pins the same provepr (and so the same guide and plan format) that wrote this file.
    install = f"orvix-provepr=={version('orvix-provepr')}"
    return WORKFLOW.replace("PLAYWRIGHT_VERSION", version("playwright")).replace("INSTALL_SPEC", install)


def unedited(workflow: str) -> bool:
    """True if `workflow` is a template `init` wrote (any version), with only its version pins differing."""
    workflow = PIN.sub("INSTALL_SPEC", workflow)
    return re.sub(r"playwright@\S+", "playwright@PLAYWRIGHT_VERSION", workflow) in (WORKFLOW, *OLD_WORKFLOWS)


def pins_older(workflow: str) -> bool:
    return any(v != version("orvix-provepr") for v in PIN.findall(workflow))


def stale(root: Path) -> list[str]:
    """Notes on files `provepr init` wrote that are behind the installed version."""
    notes = []
    agents = root / "AGENTS.md"
    if agents.exists() and BEGIN in (text := agents.read_text()) and upsert_block(text) != text:
        notes.append("AGENTS.md has an older provepr block: run `provepr init` to refresh it.")
    wf = root / ".github/workflows/provepr.yml"
    if wf.exists() and (old := wf.read_text()) != pinned():
        if unedited(old):
            notes.append("provepr.yml is out of date: run `provepr init` to update it.")
        elif pins_older(old):
            notes.append(EDITED)
    return notes


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
    if missing := [line for line in IGNORED if line not in (gitignore or "").splitlines()]:
        base = (gitignore or "").rstrip()
        write(".gitignore", (base + "\n" if base else "") + "\n".join(missing) + "\n", gitignore)
    else:
        done.append("unchanged  .gitignore")

    if ci:
        wf = read(".github/workflows/provepr.yml")
        keep = wf is not None and not unedited(wf)  # never clobber edits
        write(".github/workflows/provepr.yml", wf if keep else pinned(), wf)
        if keep and pins_older(wf):
            done.append(f"note: {EDITED}")
    return done
