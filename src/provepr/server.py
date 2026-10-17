"""MCP tools so a coding agent can run checks and read the evidence itself.

The server never calls an LLM: it runs the browser and returns facts. The agent's own
model decides whether each claim really holds.
"""

import asyncio
import io
import json
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer
from PIL import Image as PILImage

from provepr import report, runner

DEFAULT_PLAN = ".provepr/plan.yaml"
FRAME_WIDTH = 800  # keeps each frame to a few hundred image tokens

mcp = MCPServer(
    "provepr",
    instructions=(
        "Checks a code change's claims in a real browser. Write the claims and steps to .provepr/plan.yaml "
        "BEFORE running, then call run_proof, read the report, fix the code (never weaken the plan), and rerun."
    ),
)


def runs_dir(plan_path: str) -> Path:
    return Path(plan_path).resolve().parent / "runs"


def find_run(plan_path: str, run: str | None) -> Path:
    runs = runs_dir(plan_path)
    if run:
        path = runs / Path(run).name
    else:
        done = sorted(p for p in runs.glob("*") if (p / "report.json").exists()) if runs.exists() else []
        if not done:
            raise ValueError(f"no runs under {runs}; call run_proof first")
        path = done[-1]
    if not (path / "report.json").exists():
        raise ValueError(f"run not found: {path}")
    return path


def png(path: Path, width: int = FRAME_WIDTH) -> Image:
    with PILImage.open(path) as im:
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)))
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
    return Image(data=buf.getvalue(), format="png")


@mcp.tool()
async def run_proof(plan_path: str = DEFAULT_PLAN, base_url: str | None = None) -> list:
    """Run every claim in the plan in a headless browser and return the report plus a contact sheet.

    plan_path: path to the plan YAML (relative to the project root, default .provepr/plan.yaml).
    base_url: overrides the plan's base_url, e.g. http://localhost:3000.
    """
    # Playwright's sync API cannot run on the event loop thread.
    run_dir, rep = await asyncio.to_thread(runner.run_plan, Path(plan_path), base_url)
    md = report.write(run_dir, rep)
    return [md, png(run_dir / "contact.png", width=1280)]


@mcp.tool()
async def get_report(plan_path: str = DEFAULT_PLAN, run: str | None = None, include_json: bool = False) -> list:
    """Return the latest (or a named) run's report and contact sheet.

    include_json: also return the full report.json (every step, error and timing).
    """
    run_dir = find_run(plan_path, run)
    out = [(run_dir / "report.md").read_text(), png(run_dir / "contact.png", width=1280)]
    if include_json:
        out.insert(1, (run_dir / "report.json").read_text())
    return out


@mcp.tool()
async def get_frames(
    claim_id: str, steps: list[int] | None = None, plan_path: str = DEFAULT_PLAN, run: str | None = None
) -> list:
    """Return screenshots for one claim. One frame is taken after every step.

    steps: step numbers to fetch. Default: the failing step and the one before it,
    or the last step if the claim passed.
    """
    run_dir = find_run(plan_path, run)
    rep = json.loads((run_dir / "report.json").read_text())
    claim = next((c for c in rep["claims"] if c["id"] == claim_id), None)
    if claim is None:
        raise ValueError(f"no claim '{claim_id}'; claims: {[c['id'] for c in rep['claims']]}")
    recs = [s for s in claim["steps"] if s.get("frame")]
    if steps:
        recs = [s for s in recs if s["n"] in steps]
    else:
        bad = next((i for i, s in enumerate(recs) if not s["ok"]), None)
        recs = recs[max(bad - 1, 0) : bad + 1] if bad is not None else recs[-1:]
    out: list = []
    for s in recs:
        out += [
            f"step {s['n']} `{report.step_label(s['step'])}`: {'ok' if s['ok'] else 'FAILED ' + s['error']}",
            png(run_dir / s["frame"]),
        ]
    return out or [f"claim '{claim_id}' has no frames"]
