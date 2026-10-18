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

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

from provepr import plan as planfile

STEP_TIMEOUT_MS = 5000
VIEWPORT = {"width": 1280, "height": 720}


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
    raise ValueError(f"no locator in {spec}; use one of {planfile.LOCATOR_KEYS}")


def url(base_url: str, path) -> str:
    return base_url.rstrip("/") + "/" + str(path).lstrip("/")


def http_step(page, base_url: str, arg: dict) -> str:
    """Call the app's API with the page's cookies. Returns a one-line summary; raises if expectations fail."""
    method = str(arg.get("method", "GET")).upper()
    resp = page.request.fetch(
        url(base_url, arg["path"]),
        method=method,
        data=arg.get("json"),
        headers=arg.get("headers"),
        fail_on_status_code=False,
        timeout=STEP_TIMEOUT_MS,
    )
    body = resp.text()
    summary = f"{method} {arg['path']} -> HTTP {resp.status}: {body[:200]}"
    want = arg.get("expect_status")
    if (resp.status != want) if want else resp.status >= 400:
        raise AssertionError(f"expected HTTP {want or '2xx/3xx'}; got {summary}")
    if "expect_body" in arg and str(arg["expect_body"]) not in body:
        raise AssertionError(f"response body does not contain {str(arg['expect_body'])!r}; got {summary}")
    return summary


def do_step(page, base_url: str, step: dict) -> str | None:
    """Execute one (already validated) step. Returns an optional note; raises on failure."""
    ((action, arg),) = step.items()
    t = STEP_TIMEOUT_MS
    match action:
        case "http":
            return http_step(page, base_url, arg)
        case "goto":
            page.goto(url(base_url, arg))
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
    return None


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
        result.update(status="UNVERIFIED", reasons=["no steps, so nothing was checked"])
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
            if note := do_step(page, base_url, step):
                rec["note"] = note
        except (PlaywrightError, AssertionError, ValueError, KeyError, TypeError) as e:
            rec.update(ok=False, error=short_error(e))
        rec["ms"] = round((time.monotonic() - start) * 1000)
        frame = frames / f"{i:02d}.png"
        try:
            if "http" not in step:  # an API call changes nothing on screen
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


def run_plan(plan_path: Path, base_url: str | None = None, only: list[str] | None = None) -> tuple[Path, dict]:
    """Run the plan's claims (all, or just the ids in `only`). Returns (run_dir, report)."""
    plan_path = plan_path.resolve()
    plan = planfile.load(plan_path, base_url)
    base_url = base_url or os.environ.get("PROVEPR_BASE_URL") or plan["base_url"]
    claims_to_run = plan["claims"]
    if only:
        unknown = set(only) - {c["id"] for c in claims_to_run}
        if unknown:
            raise ValueError(f"no such claim(s): {', '.join(sorted(unknown))}")
        claims_to_run = [c for c in claims_to_run if c["id"] in only]

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
            claims = [run_claim(browser, base_url, c, out) for c in claims_to_run]
            browser.close()
    finally:
        if server and server.poll() is None:
            os.killpg(server.pid, signal.SIGTERM)
            server.wait(timeout=10)
    shutil.rmtree(out / "videos" / "_tmp", ignore_errors=True)

    counts = {s: sum(c["status"] == s for c in claims) for s in ("PASS", "FAIL", "UNVERIFIED")}
    report = {"run": out.name, "base_url": base_url, "plan": str(plan_path), "counts": counts, "claims": claims}
    return out, report
