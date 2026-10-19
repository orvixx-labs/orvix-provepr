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

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

from provepr import plan as planfile

STEP_TIMEOUT_MS = 5000
CMD_TIMEOUT_S = 30
VIEWPORT = {"width": 1280, "height": 720}
KEEP_RUNS = 5  # older run folders are deleted when a new run starts


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


def mock_step(page, base_url: str, arg: dict, mocked: list) -> str:
    """Fake, fail or delay the page's matching requests for the rest of the claim."""
    path = str(arg["path"])
    method = str(arg.get("method", "")).upper()
    fake = {k: arg[k] for k in ("status", "body", "json") if k in arg}

    def handle(route):
        if method and route.request.method != method:
            return route.fallback()
        try:
            if arg.get("delay"):
                page.wait_for_timeout(int(arg["delay"]))
            if arg.get("fail"):
                mocked.append(route.request)  # a failure we caused is not the app's bug
                route.abort("failed")
            elif fake:
                mocked.append(route.request)
                route.fulfill(status=fake.get("status", 200), body=fake.get("body"), json=fake.get("json"))
            else:
                route.continue_()
        except PlaywrightError:
            pass  # the page closed while the request was held

    page.route(path if "://" in path else url(base_url, path), handle)
    what = "fail" if arg.get("fail") else f"answer {fake}" if fake else "pass through"
    return f"mock {method or 'any'} {path}: {what}" + (f" after {arg['delay']} ms" if arg.get("delay") else "")


def cmd_step(root: Path, arg: dict) -> tuple[str, str | None]:
    """Run a shell command from the project root. Returns (terminal transcript, problem or None)."""
    timeout = arg.get("timeout", CMD_TIMEOUT_S)
    proc = subprocess.Popen(
        str(arg["run"]),
        shell=True,
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        start_new_session=True,  # so a timeout can kill the command's whole process tree
    )
    try:
        out, _ = proc.communicate(str(arg.get("stdin", "")), timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        return f"$ {arg['run']}\n{out}[killed after {timeout}s]", f"timed out after {timeout}s"
    transcript = f"$ {arg['run']}\n{out}[exit {proc.returncode}]"
    last = (out.strip().splitlines() or [""])[-1][:200]
    want = arg.get("expect_exit", 0)
    if proc.returncode != want:
        return transcript, f"expected exit {want}, got {proc.returncode}; last output: {last}"
    if "expect_output" in arg and str(arg["expect_output"]) not in out:
        return transcript, f"output does not contain {str(arg['expect_output'])!r}; last output: {last}"
    return transcript, None


def terminal_frame(transcript: str, path: Path) -> None:
    """Draw the end of a transcript as a terminal screenshot, so CLI steps get frames like browser steps."""
    try:
        font = ImageFont.truetype("DejaVuSansMono.ttf", 16)
    except OSError:
        font = ImageFont.load_default(size=16)
    rows = VIEWPORT["height"] // 20 - 1
    lines = [ln[:130] for ln in transcript.splitlines()][-rows:]
    im = Image.new("RGB", (VIEWPORT["width"], VIEWPORT["height"]), (24, 24, 27))
    ImageDraw.Draw(im).multiline_text((12, 10), "\n".join(lines), fill=(228, 228, 231), font=font, spacing=4)
    im.save(path)


def do_step(page, base_url: str, step: dict, mocked: list) -> str | None:
    """Execute one (already validated) browser or http step. Returns an optional note; raises on failure."""
    ((action, arg),) = step.items()
    t = STEP_TIMEOUT_MS
    match action:
        case "http":
            return http_step(page, base_url, arg)
        case "mock":
            return mock_step(page, base_url, arg, mocked)
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


def needs_browser(claim: dict) -> bool:
    return any(planfile.action_of(s) != "cmd" for s in claim.get("steps") or [])


def run_claim(browser, base_url: str | None, claim: dict, out: Path, root: Path) -> dict:
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
    mocked = []  # requests a mock step answered or failed on purpose; they don't count against the app
    page = None
    if needs_browser(claim):
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
                and r.request not in mocked
                and result["network_errors"].append({"method": r.request.method, "url": r.url, "status": r.status})
            ),
        )
        page.on(
            "requestfailed",
            lambda r: (
                "ERR_ABORTED" not in (r.failure or "")
                and r not in mocked
                and result["network_errors"].append(
                    {"method": r.method, "url": r.url, "status": None, "failure": r.failure}
                )
            ),
        )

    for i, step in enumerate(steps, 1):
        action = planfile.action_of(step)
        rec = {"n": i, "step": step, "ok": True}
        start = time.monotonic()
        try:
            if action == "cmd":
                rec["output"], problem = cmd_step(root, step["cmd"])
                if problem:
                    raise AssertionError(problem)
            elif note := do_step(page, base_url, step, mocked):
                rec["note"] = note
        except (PlaywrightError, AssertionError, ValueError, KeyError, TypeError) as e:
            rec.update(ok=False, error=short_error(e))
        rec["ms"] = round((time.monotonic() - start) * 1000)
        frame = frames / f"{i:02d}.png"
        try:
            if action == "cmd":
                terminal_frame(rec["output"], frame)
                rec["frame"] = str(frame.relative_to(out))
                rec["output"] = rec["output"][-4000:]
            elif action not in ("http", "mock"):  # those change nothing on screen
                page.screenshot(path=str(frame))
                rec["frame"] = str(frame.relative_to(out))
        except PlaywrightError:
            pass
        result["steps"].append(rec)
        if not rec["ok"]:
            break

    video = page and page.video
    if page:
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


def prune_old_runs(runs: Path, keep: int = KEEP_RUNS) -> None:
    """Delete all but the newest `keep` run folders (names sort by time)."""
    if runs.exists():
        for old in sorted(p for p in runs.iterdir() if p.is_dir())[:-keep]:
            shutil.rmtree(old, ignore_errors=True)


def run_plan(plan_path: Path, base_url: str | None = None, only: list[str] | None = None) -> tuple[Path, dict]:
    """Run the plan's claims (all, or just the ids in `only`). Returns (run_dir, report)."""
    plan_path = plan_path.resolve()
    plan = planfile.load(plan_path, base_url)
    base_url = base_url or os.environ.get("PROVEPR_BASE_URL") or plan.get("base_url")
    root = plan_path.parent.parent  # the directory that holds .provepr/, i.e. the project root
    claims_to_run = plan["claims"]
    if only:
        unknown = set(only) - {c["id"] for c in claims_to_run}
        if unknown:
            raise ValueError(f"no such claim(s): {', '.join(sorted(unknown))}")
        claims_to_run = [c for c in claims_to_run if c["id"] in only]

    runs = plan_path.parent / "runs"
    prune_old_runs(runs, KEEP_RUNS - 1)  # leaves room for this run
    out = runs / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True)

    server = None
    if plan.get("start"):
        log = (out / "app.log").open("w")
        server = subprocess.Popen(
            plan["start"],
            shell=True,
            cwd=root,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        try:
            if base_url:  # a plan of only cmd steps may have no URL to wait for
                wait_for_url(base_url, timeout_s=plan.get("start_timeout", 30))
        except TimeoutError as e:
            tail = (out / "app.log").read_text()[-1500:] if server else ""
            raise TimeoutError(f"{e}\n--- app.log (tail) ---\n{tail}" if tail else str(e)) from None
        if any(needs_browser(c) for c in claims_to_run):
            with sync_playwright() as pw:
                browser = launch(pw)
                claims = [run_claim(browser, base_url, c, out, root) for c in claims_to_run]
                browser.close()
        else:
            claims = [run_claim(None, base_url, c, out, root) for c in claims_to_run]
    finally:
        if server and server.poll() is None:
            os.killpg(server.pid, signal.SIGTERM)
            server.wait(timeout=10)
    shutil.rmtree(out / "videos" / "_tmp", ignore_errors=True)

    counts = {s: sum(c["status"] == s for c in claims) for s in ("PASS", "FAIL", "UNVERIFIED")}
    try:
        head = planfile.git(root, "rev-parse", "--short", "HEAD").stdout.strip() or None
    except OSError:  # git not installed
        head = None
    report = {"run": out.name, "base_url": base_url, "plan": str(plan_path), "commit": head, "counts": counts}
    report["claims"] = claims
    return out, report
