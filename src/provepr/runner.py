"""Run a .provepr/plan.yaml in a real browser and collect deterministic evidence."""

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

from provepr import plan as planfile

STEP_TIMEOUT_MS = 5000
SLICE_MS = 250  # expectations retry in slices this long, so a claim that already failed stops waiting
CMD_TIMEOUT_S = 30
VIEWPORT = {"width": 1280, "height": 720}  # terminal frames for cmd steps
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


def failure_reasons(result: dict) -> list[str]:
    """Why the claim FAILs regardless of its steps: page errors, HTTP 5xx, failed requests."""
    reasons = [f"uncaught page error: {e}" for e in result["page_errors"]]
    reasons += [
        f"HTTP {n['status']} {n['method']} {n['url']}" for n in result["network_errors"] if (n["status"] or 0) >= 500
    ]
    reasons += [
        f"request failed: {n['method']} {n['url']} ({n['failure']})"
        for n in result["network_errors"]
        if n["status"] is None
    ]
    return reasons


def patiently(check, failed) -> None:
    """Retry an expectation for up to STEP_TIMEOUT_MS, but give up as soon as `failed()` has a reason:
    the claim is a FAIL anyway, so waiting out the timeout only costs time."""
    deadline = time.monotonic() + STEP_TIMEOUT_MS / 1000
    while True:
        left_ms = (deadline - time.monotonic()) * 1000
        try:
            return check(max(1, min(SLICE_MS, left_ms)))
        except AssertionError as e:
            if time.monotonic() >= deadline:
                raise
            if reasons := failed():
                raise AssertionError(f"stopped waiting, the claim already failed ({reasons[0]}): {e}") from e


def upload_files(root: Path, arg: dict) -> list[str]:
    names = arg["file"] if isinstance(arg["file"], list) else [arg["file"]]
    paths = [root / str(n) for n in names]
    if missing := [str(n) for n, path in zip(names, paths, strict=True) if not path.is_file()]:
        raise ValueError(f"no such file to upload: {', '.join(missing)} (paths are from the project root)")
    return [str(path) for path in paths]


def do_step(page, base_url: str, step: dict, mocked: list, failed=list, root: Path = Path(".")) -> str | None:
    """Execute one (already validated) browser or http step. Returns an optional note; raises on failure.

    failed: returns the claim's failure reasons so far; expectations stop waiting once there are any.
    root: the project root, which `upload` paths are relative to.
    """
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
        case "hover":
            locate(page, arg).hover(timeout=t)
        case "check":
            locate(page, arg).check(timeout=t)
        case "uncheck":
            locate(page, arg).uncheck(timeout=t)
        case "upload":
            locate(page, arg).set_input_files(upload_files(root, arg), timeout=t)
        case "fill":
            locate(page, arg).fill(str(arg["value"]), timeout=t)
        case "select":
            locate(page, arg).select_option(str(arg["value"]), timeout=t)
        case "press":
            page.keyboard.press(str(arg))
        case "expect_text":
            patiently(lambda ms: expect(page.locator("body")).to_contain_text(str(arg), timeout=ms), failed)
        case "expect_no_text":
            patiently(lambda ms: expect(page.locator("body")).not_to_contain_text(str(arg), timeout=ms), failed)
        case "expect_visible":
            patiently(lambda ms: expect(locate(page, arg).first).to_be_visible(timeout=ms), failed)
        case "expect_value":
            patiently(lambda ms: expect(locate(page, arg)).to_have_value(str(arg["value"]), timeout=ms), failed)
        case "expect_count":
            patiently(lambda ms: expect(locate(page, arg)).to_have_count(arg["count"], timeout=ms), failed)
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


def needs_browser(steps: list) -> bool:
    return any(planfile.action_of(s) != "cmd" for s in steps)


@dataclass
class Settings:
    """What every claim in a run shares."""

    base_url: str | None
    out: Path
    root: Path
    video: bool = False
    viewport: object = None  # the plan's default; a claim can override it
    before_each: list = field(default_factory=list)
    setup: dict | None = None  # the setup result, if the plan has one
    state: dict | None = None  # cookies and storage the setup left, given to every claim that isn't `fresh`


def new_result(cid: str, says: str) -> dict:
    return {"id": cid, "says": says, "steps": [], "console_errors": [], "page_errors": [], "network_errors": []}


def open_page(browser, result: dict, mocked: list, options: dict, state=None, video_dir: Path | None = None):
    """A fresh browser context and page whose console, page and network errors are recorded in `result`."""
    recording = {"record_video_dir": str(video_dir), "record_video_size": options["viewport"]} if video_dir else {}
    ctx = browser.new_context(**options, storage_state=state, **recording)
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
    return ctx, page


def run_steps(page, steps: list, result: dict, mocked: list, s: Settings, frames: Path | None) -> list[dict]:
    """Run steps in order until one fails. Returns a record per step that ran; `frames` gets a screenshot
    per step (None: no screenshots)."""
    recs = []
    for i, step in enumerate(steps, 1):
        action = planfile.action_of(step)
        rec = {"n": i, "step": step, "ok": True}  # the report shows the step as written, ${NAME} and all
        live = planfile.expand(step)
        if action == "cmd":
            live["cmd"]["run"] = step["cmd"]["run"]  # the shell expands ${NAME} itself, quoting included
        start = time.monotonic()
        try:
            if action == "cmd":
                rec["output"], problem = cmd_step(s.root, live["cmd"])
                if problem:
                    raise AssertionError(problem)
            elif note := do_step(page, s.base_url, live, mocked, lambda: failure_reasons(result), s.root):
                rec["note"] = note
        except (PlaywrightError, AssertionError, ValueError, KeyError, TypeError) as e:
            rec.update(ok=False, error=short_error(e))
        rec["ms"] = round((time.monotonic() - start) * 1000)
        frame = frames / f"{i:02d}.png" if frames else None
        try:
            if action == "cmd":
                if frame:
                    terminal_frame(rec["output"], frame)
                    rec["frame"] = str(frame.relative_to(s.out))
                rec["output"] = rec["output"][-4000:]
            elif frame and action not in ("http", "mock"):  # those change nothing on screen
                page.screenshot(path=str(frame))
                rec["frame"] = str(frame.relative_to(s.out))
        except PlaywrightError:
            pass
        recs.append(rec)
        if not rec["ok"]:
            break
    return recs


SETUP_ID = "_setup"  # claim ids can't start with "_", so its frames folder never clashes with a claim's


def run_setup(browser, steps: list, s: Settings) -> tuple[dict, dict | None]:
    """Run the plan's `setup` steps once. Returns (result, the browser state they left, e.g. a login)."""
    result = new_result(SETUP_ID, "setup: runs once, before every claim")
    frames = s.out / "frames" / SETUP_ID
    frames.mkdir(parents=True)
    mocked, state, ctx, page = [], None, None, None
    if browser:
        ctx, page = open_page(browser, result, mocked, planfile.context_options(s.viewport))
    result["steps"] = run_steps(page, steps, result, mocked, s, frames)
    if ctx:
        state = ctx.storage_state()
        ctx.close()
    reasons = [f"setup step {x['n']} failed: {x['error']}" for x in result["steps"] if not x["ok"]]
    reasons += failure_reasons(result)
    result.update(status="FAIL" if reasons else "PASS", reasons=reasons)
    return result, state


def run_claim(browser, claim: dict, s: Settings) -> dict:
    cid = claim["id"]
    steps = claim.get("steps") or []
    result = new_result(cid, claim.get("says", ""))
    result["video"] = None
    if not steps:
        result.update(status="UNVERIFIED", reasons=["no steps, so nothing was checked"])
        return result
    fresh = claim.get("fresh", False)
    if s.setup and s.setup["status"] == "FAIL" and not fresh:
        result.update(status="FAIL", reasons=[f"did not run, the setup failed: {s.setup['reasons'][0]}"])
        return result

    frames = s.out / "frames" / cid
    frames.mkdir(parents=True)
    mocked = []  # requests a mock step answered or failed on purpose; they don't count against the app
    ctx = page = None
    if needs_browser(steps + s.before_each):
        options = planfile.context_options(claim.get("viewport", s.viewport))
        state = None if fresh else s.state
        ctx, page = open_page(browser, result, mocked, options, state, s.out / "videos" / "_tmp" if s.video else None)

    reasons = []
    if s.before_each:
        result["before_each"] = run_steps(page, s.before_each, result, mocked, s, frames=None)
        reasons += [f"before_each step {x['n']} failed: {x['error']}" for x in result["before_each"] if not x["ok"]]
    if not reasons:
        result["steps"] = run_steps(page, steps, result, mocked, s, frames)

    recorded = page and page.video
    if ctx:
        ctx.close()
    if recorded:
        dest = s.out / "videos" / f"{cid}.webm"
        try:
            shutil.move(recorded.path(), dest)
            result["video"] = str(dest.relative_to(s.out))
        except OSError:
            pass  # the browser wrote no video (e.g. its ffmpeg is missing); the frames are still there

    reasons += [f"step {x['n']} failed: {x['error']}" for x in result["steps"] if not x["ok"]]
    reasons += failure_reasons(result)
    result["status"] = "FAIL" if reasons else "PASS"
    result["reasons"] = reasons
    return result


_install_lock = threading.Lock()  # parallel workers must not download Chromium twice at once


def launch(pw):
    """Launch Chromium, downloading it on first use (plugin installs never ran `playwright install`)."""
    try:
        return pw.chromium.launch()
    except PlaywrightError as e:
        if "Executable doesn't exist" not in str(e):
            raise
    with _install_lock:
        # stdout goes to stderr: under MCP, stdout is the protocol stream.
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], stdout=sys.stderr, check=True)
    return pw.chromium.launch()


def run_claims(claims: list[dict], setup: list | None, s: Settings, workers: int) -> list[dict]:
    """Run the setup once, then the claims, split over `workers` threads each with its own browser, so slow
    claims overlap.

    Turning parallel on says the claims don't share data, so a FAIL is reported as is. Its note points to
    rerunning it alone, in case two claims did clash.
    """
    if setup:
        with sync_playwright() as pw:
            browser = launch(pw) if needs_browser(setup) else None
            try:
                s.setup, s.state = run_setup(browser, setup, s)
            finally:
                if browser:
                    browser.close()

    def batch(chunk: list[dict]) -> list[dict]:
        if not any(needs_browser((c.get("steps") or []) + s.before_each) for c in chunk):
            return [run_claim(None, c, s) for c in chunk]
        with sync_playwright() as pw:  # one per thread: sync Playwright objects can't cross threads
            browser = launch(pw)
            try:
                return [run_claim(browser, c, s) for c in chunk]
            finally:
                browser.close()

    workers = min(workers, len(claims))
    if workers <= 1:
        return batch(claims)
    with ThreadPoolExecutor(workers) as pool:
        done = {r["id"]: r for rs in pool.map(batch, [claims[i::workers] for i in range(workers)]) for r in rs}
    for r in done.values():
        if r["status"] == "FAIL":
            r["note"] = f"ran in parallel: if this looks wrong, rerun it alone with --only {r['id']}"
    return [done[c["id"]] for c in claims]


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


def redact(obj, secrets: list[str]):
    """Replace every secret value in the report's text with ***."""
    if isinstance(obj, str):
        for secret in secrets:
            obj = obj.replace(secret, "***")
        return obj
    if isinstance(obj, dict):
        return {k: redact(v, secrets) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v, secrets) for v in obj]
    return obj


def run_plan(
    plan_path: Path, base_url: str | None = None, only: list[str] | None = None, video: bool = False
) -> tuple[Path, dict]:
    """Run the plan's claims (all, or just the ids in `only`). Returns (run_dir, report).

    video: record a .webm per claim. Off by default: agents read frames, not video.
    """
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

    # ${NAME} in steps comes from the environment, so secrets stay out of the committed plan.
    shared = (plan.get("setup") or []) + (plan.get("before_each") or [])
    secret_names = planfile.env_names([c.get("steps") or [] for c in claims_to_run] + shared)
    if missing := sorted((secret_names | planfile.env_names(base_url)) - set(os.environ)):
        raise ValueError(
            "the plan reads these environment variables, but they are not set: "
            + ", ".join(missing)
            + " (in CI, add them as repository secrets and pass them to the provepr step with `env:`)"
        )
    base_url = planfile.expand(base_url)
    secrets = sorted({os.environ[n] for n in secret_names if len(os.environ[n]) >= 4}, key=len, reverse=True)

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
    settings = Settings(base_url, out, root, video, plan.get("viewport"), plan.get("before_each") or [])
    try:
        try:
            if base_url:  # a plan of only cmd steps may have no URL to wait for
                wait_for_url(base_url, timeout_s=plan.get("start_timeout", 30))
        except TimeoutError as e:
            tail = (out / "app.log").read_text()[-1500:] if server else ""
            raise TimeoutError(f"{e}\n--- app.log (tail) ---\n{tail}" if tail else str(e)) from None
        claims = run_claims(claims_to_run, plan.get("setup"), settings, plan.get("parallel", 1))
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
    if settings.setup:
        report["setup"] = settings.setup
    report["claims"] = claims
    return out, redact(report, secrets)
