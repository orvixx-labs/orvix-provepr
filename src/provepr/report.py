"""Turn a run into report.json, a compact report.md for agents, and one contact sheet."""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

THUMB = (640, 360)
COLORS = {"PASS": (22, 128, 61), "FAIL": (190, 30, 45), "UNVERIFIED": (120, 120, 120)}
MAX_LISTED = 5


def key_frame(claim: dict) -> str | None:
    """The frame that best shows the claim's outcome: the failing step, else the last one."""
    frames = [s for s in claim["steps"] if s.get("frame")]
    failed = [s for s in frames if not s["ok"]]
    return (failed or frames or [{}])[-1].get("frame")


def contact_sheet(run_dir: Path, report: dict) -> Path:
    claims = report["claims"]
    cols = 1 if len(claims) == 1 else 2
    rows = -(-len(claims) // cols)
    caption_h = 34
    sheet = Image.new("RGB", (cols * THUMB[0], rows * (THUMB[1] + caption_h)), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=18)
    for i, c in enumerate(claims):
        x, y = (i % cols) * THUMB[0], (i // cols) * (THUMB[1] + caption_h)
        draw.rectangle([x, y, x + THUMB[0] - 1, y + caption_h - 1], fill=COLORS[c["status"]])
        failed = next((s["n"] for s in c["steps"] if not s["ok"]), None)
        caption = f"{c['status']}  {c['id']}" + (f"  (step {failed})" if failed else "")
        draw.text((x + 8, y + 7), caption, fill="white", font=font)
        frame = key_frame(c)
        if frame:
            with Image.open(run_dir / frame) as im:
                im.thumbnail(THUMB)
                sheet.paste(im, (x, y + caption_h))
        else:
            why = "API-only claim" if c["steps"] else "not run"
            draw.text((x + 8, y + caption_h + 8), f"no screenshots ({why})", fill="gray", font=font)
    path = run_dir / "contact.png"
    sheet.save(path, optimize=True)
    return path


def step_label(step: dict) -> str:
    action, arg = next(iter(step.items())) if isinstance(step, dict) and step else ("?", step)
    return f"{action} {json.dumps(arg) if isinstance(arg, dict) else arg}"


def to_markdown(report: dict) -> str:
    c = report["counts"]
    lines = [
        f"# provepr run {report['run']}",
        f"{c['PASS']} PASS · {c['FAIL']} FAIL · {c['UNVERIFIED']} UNVERIFIED  (base_url {report['base_url']})",
        "",
    ]
    for cl in report["claims"]:
        lines.append(f"## {cl['status']} `{cl['id']}`: {cl['says']}")
        if cl["steps"]:
            ok = sum(s["ok"] for s in cl["steps"])
            lines.append(f"- steps passed: {ok}/{len(cl['steps'])}")
        for s in cl["steps"]:
            if not s["ok"]:
                lines.append(f"- FAILED step {s['n']} `{step_label(s['step'])}`: {s['error']}")
        lines += [f"- {r}" for r in cl["reasons"] if not r.startswith("step ")][:MAX_LISTED]
        lines += [f"- console error: {e}" for e in dict.fromkeys(cl["console_errors"])][:MAX_LISTED]
        lines += [
            f"- HTTP {n['status']} {n['method']} {n['url']}"
            for n in cl["network_errors"]
            if n["status"] and n["status"] < 500
        ][:MAX_LISTED]
        if cl.get("video"):
            lines.append(f"- evidence: {cl['video']}, frames/{cl['id']}/")
        lines.append("")
    return "\n".join(lines)


def write(run_dir: Path, report: dict) -> str:
    (run_dir / "report.json").write_text(json.dumps(report, indent=2))
    md = to_markdown(report)
    (run_dir / "report.md").write_text(md)
    contact_sheet(run_dir, report)
    return md
