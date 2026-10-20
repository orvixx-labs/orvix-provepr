"""Turn a run into report.json, a compact report.md for agents, and one contact sheet."""

import contextlib
import json
import shutil
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

THUMB = (640, 360)
COLORS = {"PASS": (22, 128, 61), "FAIL": (190, 30, 45), "UNVERIFIED": (120, 120, 120)}
MAX_LISTED = 5
PIXEL_NOISE = 24  # per-pixel difference below this is anti-aliasing, not a change
CHANGED_SHARE = 0.001  # a frame counts as changed when more than 0.1% of its pixels differ


def key_frame(claim: dict) -> str | None:
    """The frame that best shows the claim's outcome: the failing step, else the last one."""
    frames = [s for s in claim["steps"] if s.get("frame")]
    failed = [s for s in frames if not s["ok"]]
    return (failed or frames or [{}])[-1].get("frame")


def baseline_dir(report: dict) -> Path:
    return Path(report["plan"]).parent / "baseline"


def save_baseline(run_dir: Path, report: dict) -> None:
    """Copy this run's frames to .provepr/baseline/, replacing only the claims that ran."""
    base = baseline_dir(report)
    meta_path = base / "baseline.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"claims": {}}
    meta.update(run=report["run"], commit=report.get("commit"))
    for c in report["claims"]:
        shutil.rmtree(base / c["id"], ignore_errors=True)
        meta["claims"].pop(c["id"], None)
        if (run_dir / "frames" / c["id"]).exists():
            shutil.copytree(run_dir / "frames" / c["id"], base / c["id"], ignore=shutil.ignore_patterns("*.diff.png"))
            meta["claims"][c["id"]] = [s["step"] for s in c["steps"]]
    base.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2))


def changed_share(old: Path, new: Path, diff_path: Path) -> float:
    """Share of pixels that differ. Writes the new frame, faded, with changed pixels in red."""
    with Image.open(old) as a, Image.open(new) as b:
        a, b = a.convert("RGB"), b.convert("RGB")
        if a.size != b.size:
            b.save(diff_path)
            return 1.0
        mask = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > PIXEL_NOISE else 0)
        share = mask.histogram()[255] / (b.width * b.height)
        if share > CHANGED_SHARE:
            out = Image.blend(b, Image.new("RGB", b.size, "white"), 0.6)
            out.paste((230, 0, 0), mask=mask)
            out.save(diff_path)
        return share


def compare_baseline(run_dir: Path, report: dict) -> None:
    """Mark each frame that looks different from the same step in .provepr/baseline/.

    A frame is compared only when every step up to it is the same as in the baseline;
    otherwise the screens are not expected to match.
    """
    base = baseline_dir(report)
    if not (base / "baseline.json").exists():
        report["baseline"] = None
        return
    meta = json.loads((base / "baseline.json").read_text())
    report["baseline"] = {"run": meta.get("run"), "commit": meta.get("commit")}
    for c in report["claims"]:
        old_steps = meta["claims"].get(c["id"])
        if old_steps is None:
            continue
        for s in c["steps"]:
            old = base / c["id"] / f"{s['n']:02d}.png"
            same_path = old_steps[: s["n"]] == [x["step"] for x in c["steps"][: s["n"]]]
            if not s.get("frame") or not same_path or not old.exists():
                continue
            diff = (run_dir / s["frame"]).with_suffix(".diff.png")
            share = changed_share(old, run_dir / s["frame"], diff)
            if share > CHANGED_SHARE:
                s["visual_change"] = {"percent": round(share * 100, 2), "diff": str(diff.relative_to(run_dir))}


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
        if any(s.get("visual_change") for s in c["steps"]):
            caption += "  · looks different"
        draw.text((x + 8, y + 7), caption, fill="white", font=font)
        frame = key_frame(c)
        if frame:
            with Image.open(run_dir / frame) as im:
                im.thumbnail(THUMB)
                sheet.paste(im, (x, y + caption_h))
        else:
            why = "API or mock steps only" if c["steps"] else "not run"
            draw.text((x + 8, y + caption_h + 8), f"no screenshots ({why})", fill="gray", font=font)
    path = run_dir / "contact.png"
    sheet.save(path, optimize=True)
    return path


def step_label(step: dict) -> str:
    action, arg = next(iter(step.items())) if isinstance(step, dict) and step else ("?", step)
    return f"{action} {json.dumps(arg) if isinstance(arg, dict) else arg}"


def to_markdown(report: dict) -> str:
    c = report["counts"]
    where = f"  (base_url {report['base_url']})" if report.get("base_url") else ""
    lines = [
        f"# provepr run {report['run']}",
        f"{c['PASS']} PASS · {c['FAIL']} FAIL · {c['UNVERIFIED']} UNVERIFIED{where}",
    ]
    if "baseline" in report:
        b = report["baseline"]
        lines.append(
            f"Screens compared with the baseline from run {b['run']} (commit {b['commit'] or 'unknown'})"
            if b
            else "No baseline to compare with: run `provepr run --save-baseline` on the base branch first"
        )
    lines.append("")
    for cl in report["claims"]:
        lines.append(f"## {cl['status']} `{cl['id']}`: {cl['says']}")
        if cl["steps"]:
            ok = sum(s["ok"] for s in cl["steps"])
            lines.append(f"- steps passed: {ok}/{len(cl['steps'])}")
        for s in cl["steps"]:
            if not s["ok"]:
                lines.append(f"- FAILED step {s['n']} `{step_label(s['step'])}`: {s['error']}")
        lines += [f"- {r}" for r in cl["reasons"] if not r.startswith("step ")][:MAX_LISTED]
        if cl.get("note"):
            lines.append(f"- {cl['note']}")
        lines += [f"- console error: {e}" for e in dict.fromkeys(cl["console_errors"])][:MAX_LISTED]
        lines += [
            f"- HTTP {n['status']} {n['method']} {n['url']}"
            for n in cl["network_errors"]
            if n["status"] and n["status"] < 500
        ][:MAX_LISTED]
        if changed := [s for s in cl["steps"] if s.get("visual_change")]:
            lines.append(
                f"- looks different from the baseline at step {', '.join(str(s['n']) for s in changed)}: up to "
                f"{max(s['visual_change']['percent'] for s in changed)}% of pixels changed "
                f"(diffs: frames/{cl['id']}/NN.diff.png)"
            )
        kept = [s["frame"] for s in cl["steps"] if s.get("frame")] + ([cl["video"]] if cl.get("video") else [])
        if kept:
            lines.append(f"- evidence: {', '.join(kept)}")
        lines.append("")
    return "\n".join(lines)


def frames_to_keep(claim: dict) -> set[int]:
    """Step numbers whose frame the agent needs: for a FAIL, the failing step and the one before it
    (or the last step, when it failed for another reason, e.g. an HTTP 500); plus any step that looks
    different from the baseline. A PASS needs none: the contact sheet already shows it."""
    framed = [s for s in claim["steps"] if s.get("frame")]
    keep = {s["n"] for s in framed if s.get("visual_change")}
    if claim["status"] == "FAIL" and framed:
        bad = next((i for i, s in enumerate(framed) if not s["ok"]), len(framed) - 1)
        keep |= {s["n"] for s in framed[max(bad - 1, 0) : bad + 1]}
    return keep


def prune_evidence(run_dir: Path, report: dict) -> None:
    """Delete every frame the agent won't need (see frames_to_keep), and any video."""
    for c in report["claims"]:
        keep = frames_to_keep(c)
        dropped = [s for s in c["steps"] if s.get("frame") and s["n"] not in keep]
        for s in dropped:
            (run_dir / s.pop("frame")).unlink(missing_ok=True)
        if not keep:
            shutil.rmtree(run_dir / "frames" / c["id"], ignore_errors=True)
        if c.get("video"):
            (run_dir / c["video"]).unlink(missing_ok=True)
            c["video"] = None
        if dropped:
            c["evidence"] = "only failing and changed steps keep their frames (run with --keep-evidence to keep all)"
    for d in ("frames", "videos"):
        with contextlib.suppress(OSError):  # only removed when empty
            (run_dir / d).rmdir()


def write(run_dir: Path, report: dict, keep_evidence: bool = False, compare: bool = False, save: bool = False) -> str:
    # Everything that reads frames comes before the unneeded frames are deleted.
    if compare:
        compare_baseline(run_dir, report)
    contact_sheet(run_dir, report)
    if save:
        save_baseline(run_dir, report)
    if not keep_evidence:
        prune_evidence(run_dir, report)
    (run_dir / "report.json").write_text(json.dumps(report, indent=2))
    md = to_markdown(report)
    (run_dir / "report.md").write_text(md)
    return md
