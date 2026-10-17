"""Run a .provepr/plan.yaml in a real browser and collect deterministic evidence."""

import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

STEP_TIMEOUT_MS = 5000
VIEWPORT = {"width": 1280, "height": 720}
LOCATOR_KEYS = ("role", "label", "text", "placeholder", "testid", "css")


def load_plan(path: Path) -> dict:
    plan = yaml.safe_load(path.read_text()) or {}
    claims = plan.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ValueError(f"{path}: 'claims' must be a non-empty list")
    for c in claims:
        if not isinstance(c, dict) or not c.get("id"):
            raise ValueError(f"{path}: every claim needs an 'id'")
    return plan


def locate(page, spec: dict):
    if "role" in spec:
        return page.get_by_role(spec["role"], name=spec.get("name"))
    if "label" in spec:
        return page.get_by_label(spec["label"])
    if "text" in spec:
        return page.get_by_text(spec["text"])
    if "placeholder" in spec:
        return page.get_by_placeholder(spec["placeholder"])
    if "testid" in spec:
        return page.get_by_test_id(spec["testid"])
    if "css" in spec:
        return page.locator(spec["css"])
    raise ValueError(f"no locator in {spec}; use one of {LOCATOR_KEYS}")


def do_step(page, base_url: str, step: dict) -> str:
    """Execute one step. Returns the action name; raises on failure."""
    if not isinstance(step, dict) or len(step) != 1:
        raise ValueError(f"step must be a single-key mapping, got {step!r}")
    ((action, arg),) = step.items()
    t = STEP_TIMEOUT_MS
    match action:
        case "goto":
            page.goto(base_url.rstrip("/") + "/" + str(arg).lstrip("/"))
        case "reload":
            page.reload()
        case "wait":
            page.wait_for_timeout(int(arg))
        case "click":
            locate(page, arg).click(timeout=t)
        case "fill":
            locate(page, arg).fill(str(arg["value"]), timeout=t)
        case "select":
            locate(page, arg).select_option(str(arg["value"]), timeout=t)
        case "press":
            page.keyboard.press(str(arg))
        case "expect_text":
            expect(page.locator("body")).to_contain_text(str(arg), timeout=t)
        case "expect_no_text":
            expect(page.locator("body")).not_to_contain_text(str(arg), timeout=t)
        case "expect_visible":
            expect(locate(page, arg).first).to_be_visible(timeout=t)
        case "expect_value":
            expect(locate(page, arg)).to_have_value(str(arg["value"]), timeout=t)
        case "expect_url":
            page.wait_for_url(f"**{arg}**", timeout=t)
        case _:
            raise ValueError(f"unknown step '{action}'")
    return action


def short_error(e: Exception) -> str:
    # Playwright errors carry a long call log; the first lines say what went wrong.
    text = str(e).split("Call log:")[0]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return " | ".join(lines[:3])[:400]


def run_claim(browser, base_url: str, claim: dict, out: Path) -> dict:
    cid = claim["id"]
    steps = claim.get("steps") or []
    result = {
        "id": cid,
        "says": claim.get("says", ""),
        "steps": [],
        "console_errors": [],
        "page_errors": [],
        "network_errors": [],
        "video": None,
    }
    if not steps:
        result.update(status="UNVERIFIED", reasons=["no runnable steps (not checkable in a browser yet)"])
        return result

    frames = out / "frames" / cid
    frames.mkdir(parents=True)
    ctx = browser.new_context(
        viewport=VIEWPORT, record_video_dir=str(out / "videos" / "_tmp"), record_video_size=VIEWPORT
    )
    page = ctx.new_page()
    page.on("console", lambda m: m.type == "error" and result["console_errors"].append(m.text[:300]))
    page.on("pageerror", lambda e: result["page_errors"].append(str(e)[:300]))
    page.on(
        "response",
        lambda r: (
            r.status >= 400
            and result["network_errors"].append({"method": r.request.method, "url": r.url, "status": r.status})
        ),
    )
    page.on(
        "requestfailed",
        lambda r: (
            "ERR_ABORTED" not in (r.failure or "")
            and result["network_errors"].append(
                {"method": r.method, "url": r.url, "status": None, "failure": r.failure}
            )
        ),
    )

    for i, step in enumerate(steps, 1):
        rec = {"n": i, "step": step, "ok": True}
        start = time.monotonic()
        try:
            do_step(page, base_url, step)
        except (PlaywrightError, AssertionError, ValueError, KeyError, TypeError) as e:
            rec.update(ok=False, error=short_error(e))
        rec["ms"] = round((time.monotonic() - start) * 1000)
        frame = frames / f"{i:02d}.png"
        try:
            page.screenshot(path=str(frame))
            rec["frame"] = str(frame.relative_to(out))
        except PlaywrightError:
            pass
        result["steps"].append(rec)
        if not rec["ok"]:
            break

    video = page.video
    ctx.close()
    if video:
        dest = out / "videos" / f"{cid}.webm"
        shutil.move(video.path(), dest)
        result["video"] = str(dest.relative_to(out))

    reasons = [f"step {s['n']} failed: {s['error']}" for s in result["steps"] if not s["ok"]]
    reasons += [f"uncaught page error: {e}" for e in result["page_errors"]]
    reasons += [
        f"HTTP {n['status']} {n['method']} {n['url']}" for n in result["network_errors"] if (n["status"] or 0) >= 500
    ]
    reasons += [
        f"request failed: {n['method']} {n['url']} ({n['failure']})"
        for n in result["network_errors"]
        if n["status"] is None
    ]
    result["status"] = "FAIL" if reasons else "PASS"
    result["reasons"] = reasons
    return result


def launch(pw):
    """Launch Chromium, downloading it on first use (plugin installs never ran `playwright install`)."""
    try:
        return pw.chromium.launch()
    except PlaywrightError as e:
        if "Executable doesn't exist" not in str(e):
            raise
    # stdout goes to stderr: under MCP, stdout is the protocol stream.
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], stdout=sys.stderr, check=True)
    return pw.chromium.launch()


def wait_for_url(url: str, timeout_s: float = 30) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except OSError as e:
            if getattr(e, "code", None):  # HTTPError means the server answered
                return
            if time.monotonic() > deadline:
                raise TimeoutError(f"app did not answer at {url} within {timeout_s}s") from e
            time.sleep(0.3)


def run_plan(plan_path: Path, base_url: str | None = None) -> tuple[Path, dict]:
    """Run every claim in the plan. Returns (run_dir, report)."""
    plan_path = plan_path.resolve()
    plan = load_plan(plan_path)
    base_url = base_url or os.environ.get("PROVEPR_BASE_URL") or plan.get("base_url")
    if not base_url:
        raise ValueError("no base_url: set it in the plan, pass --base-url, or set PROVEPR_BASE_URL")

    out = plan_path.parent / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True)

    # The app is started from the directory that holds .provepr/, i.e. the project root.
    server = None
    if plan.get("start"):
        log = (out / "app.log").open("w")
        server = subprocess.Popen(
            plan["start"],
            shell=True,
            cwd=plan_path.parent.parent,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        try:
            wait_for_url(base_url, timeout_s=plan.get("start_timeout", 30))
        except TimeoutError as e:
            tail = (out / "app.log").read_text()[-1500:] if server else ""
            raise TimeoutError(f"{e}\n--- app.log (tail) ---\n{tail}" if tail else str(e)) from None
        with sync_playwright() as pw:
            browser = launch(pw)
            claims = [run_claim(browser, base_url, c, out) for c in plan["claims"]]
            browser.close()
    finally:
        if server and server.poll() is None:
            os.killpg(server.pid, signal.SIGTERM)
            server.wait(timeout=10)
    shutil.rmtree(out / "videos" / "_tmp", ignore_errors=True)

    counts = {s: sum(c["status"] == s for c in claims) for s in ("PASS", "FAIL", "UNVERIFIED")}
    report = {"run": out.name, "base_url": base_url, "plan": str(plan_path), "counts": counts, "claims": claims}
    return out, report
