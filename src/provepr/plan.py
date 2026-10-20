"""Load and validate .provepr/plan.yaml. Used by `provepr check` and before every run."""

import os
import re
import subprocess
from pathlib import Path

import yaml

LOCATOR_KEYS = ("role", "label", "text", "placeholder", "testid", "css")
# step -> (required key, allowed keys) for steps that take a mapping of options
KEYED = {
    "http": ("path", {"method", "path", "json", "headers", "expect_status", "expect_body"}),
    "mock": ("path", {"path", "method", "status", "body", "json", "fail", "delay"}),
    "cmd": ("run", {"run", "stdin", "expect_exit", "expect_output", "timeout"}),
}
NUMBERS = {"expect_status", "status", "delay", "expect_exit", "timeout"}  # option keys that take a number
CHECKS = {"http", "cmd"}  # steps that check something besides expect_*
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")  # ids become file names
ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")  # ${NAME}: read from the environment at run time
VIEWPORTS = {
    "desktop": {"viewport": {"width": 1280, "height": 720}},
    "mobile": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True},
}

# step -> argument kind
STEPS = {
    "goto": "text",
    "reload": "any",
    "wait": "int",
    "press": "text",
    "click": "locator",
    "hover": "locator",
    "check": "locator",
    "uncheck": "locator",
    "upload": "locator+file",
    "expect_visible": "locator",
    "fill": "locator+value",
    "select": "locator+value",
    "expect_value": "locator+value",
    "expect_count": "locator+count",
    "expect_text": "text",
    "expect_no_text": "text",
    "expect_url": "text",
    "http": "keyed",
    "mock": "keyed",
    "cmd": "keyed",
}


def is_check(action: str) -> bool:
    return action.startswith("expect_") or action in CHECKS


def action_of(step) -> str | None:
    return next(iter(step)) if isinstance(step, dict) and step else None


def step_problem(step) -> str | None:
    if not isinstance(step, dict) or len(step) != 1:
        return f"must be a single-key mapping like `goto: /`, got {step!r}"
    ((action, arg),) = step.items()
    kind = STEPS.get(action)
    if kind is None:
        return f"unknown step '{action}'; known: {', '.join(STEPS)}"
    if kind == "text" and not isinstance(arg, str | int | float):
        return f"'{action}' takes text, got {arg!r}"
    if kind == "int" and (not isinstance(arg, int) or isinstance(arg, bool) or arg < 0):
        return f"'{action}' takes milliseconds as a whole number, got {arg!r}"
    if kind.startswith("locator"):
        if not isinstance(arg, dict) or not any(k in arg for k in LOCATOR_KEYS):
            return f"'{action}' needs a locator with one of {', '.join(LOCATOR_KEYS)}, got {arg!r}"
        if kind == "locator+value" and "value" not in arg:
            return f"'{action}' needs a 'value'"
        if kind == "locator+file" and not (
            isinstance(arg.get("file"), str) or (isinstance(arg.get("file"), list) and arg["file"])
        ):
            return f"'{action}' needs a 'file': a path from the project root, or a list of them"
        if kind == "locator+count" and (
            not isinstance(arg.get("count"), int) or isinstance(arg["count"], bool) or arg["count"] < 0
        ):
            return f"'{action}' needs a 'count' (a whole number)"
    if kind == "keyed":
        need, allowed = KEYED[action]
        if not isinstance(arg, dict) or need not in arg:
            return f"'{action}' needs at least a '{need}', got {arg!r}"
        if extra := set(arg) - allowed:
            return f"'{action}' has unknown keys {sorted(extra)}; allowed: {sorted(allowed)}"
        for k in NUMBERS & set(arg):
            if not isinstance(arg[k], int | float) or isinstance(arg[k], bool) or arg[k] < 0:
                return f"'{action}' {k} must be a number, got {arg[k]!r}"
        if action == "mock":
            faked = {"status", "body", "json"} & set(arg)
            if not faked and not arg.get("fail") and not arg.get("delay"):
                return "'mock' needs 'status', 'body', 'json', 'fail: true' or 'delay'"
            if faked and arg.get("fail"):
                return "'mock' can fail the request or answer it, not both"
            if {"body", "json"} <= set(arg):
                return "'mock' takes 'body' or 'json', not both"
    return None


def viewport_problem(value) -> str | None:
    if value is None or (isinstance(value, str) and value in VIEWPORTS):
        return None
    sizes = value.values() if isinstance(value, dict) and set(value) == {"width", "height"} else [None]
    if all(isinstance(v, int) and not isinstance(v, bool) and 100 <= v <= 4000 for v in sizes):
        return None
    return f"'viewport' must be {' or '.join(VIEWPORTS)}, or {{width: 390, height: 844}}, got {value!r}"


def context_options(value) -> dict:
    """Browser context options for a validated viewport value."""
    if isinstance(value, dict):
        return {"viewport": dict(value)}
    return VIEWPORTS[value or "desktop"]


def env_names(obj) -> set[str]:
    """Every ${NAME} the plan reads from the environment."""
    if isinstance(obj, str):
        return set(ENV_RE.findall(obj))
    if isinstance(obj, dict):
        return set().union(*(env_names(v) for v in obj.values()))
    if isinstance(obj, list):
        return set().union(*(env_names(v) for v in obj))
    return set()


def expand(obj, env=os.environ):
    """Replace ${NAME} with its environment value, everywhere in obj. Missing names are left as they are."""
    if isinstance(obj, str):
        return ENV_RE.sub(lambda m: env.get(m[1], m[0]), obj)
    if isinstance(obj, dict):
        return {k: expand(v, env) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand(v, env) for v in obj]
    return obj


def validate(plan, base_url: str | None = None) -> list[str]:
    """Every problem in the plan, as human-readable lines. Empty means valid."""
    if not isinstance(plan, dict):
        return ["the plan must be a YAML mapping with 'base_url' and 'claims'"]
    problems = []
    claims = plan.get("claims")
    if not isinstance(claims, list) or not claims:
        return ["'claims' must be a non-empty list"]
    shared = {}  # setup and before_each: steps outside any claim
    for key in ("setup", "before_each"):
        steps = plan.get(key)
        if steps is None:
            continue
        if not isinstance(steps, list) or not steps:
            problems.append(f"'{key}' must be a non-empty list of steps")
            continue
        shared[key] = steps
        for n, step in enumerate(steps, 1):
            if p := step_problem(step):
                problems.append(f"{key} step {n}: {p}")
    all_steps = [s for c in claims if isinstance(c, dict) and isinstance(c.get("steps"), list) for s in c["steps"]]
    all_steps += [s for steps in shared.values() for s in steps]
    needs_url = any(action_of(s) != "cmd" for s in all_steps)  # only cmd steps run without the app's URL
    if needs_url and not (base_url or plan.get("base_url") or os.environ.get("PROVEPR_BASE_URL")):
        problems.append("missing 'base_url' (or pass --base-url, or set PROVEPR_BASE_URL)")
    if p := viewport_problem(plan.get("viewport")):
        problems.append(p)
    parallel = plan.get("parallel", 1)
    if not isinstance(parallel, int) or isinstance(parallel, bool) or not 1 <= parallel <= 16:
        problems.append(f"'parallel' must be a whole number of workers from 1 to 16, got {parallel!r}")
    waivers = plan.get("waivers") or {}
    if not isinstance(waivers, dict) or not all(isinstance(v, str) and v.strip() for v in waivers.values()):
        problems.append("'waivers' must map claim ids to the reason, e.g. `old-id: feature removed on purpose`")
    seen = set()
    for i, c in enumerate(claims, 1):
        if not isinstance(c, dict):
            problems.append(f"claim #{i}: must be a mapping with 'id', 'says' and 'steps'")
            continue
        cid = c.get("id")
        where = f"claim '{cid}'" if cid else f"claim #{i}"
        if not isinstance(cid, str) or not ID_RE.match(cid):
            problems.append(f"{where}: 'id' must be 1-64 letters, digits, '-' or '_', starting with a letter or digit")
        elif cid in seen:
            problems.append(f"{where}: duplicate id")
        seen.add(cid)
        if not c.get("says"):
            problems.append(f"{where}: missing 'says' (what the change claims, in one sentence)")
        if p := viewport_problem(c.get("viewport")):
            problems.append(f"{where}: {p}")
        if "fresh" in c and not isinstance(c["fresh"], bool):
            problems.append(f"{where}: 'fresh' must be true or false")
        steps = c.get("steps") or []
        if not isinstance(steps, list):
            problems.append(f"{where}: 'steps' must be a list")
            continue
        for n, step in enumerate(steps, 1):
            if p := step_problem(step):
                problems.append(f"{where} step {n}: {p}")
        if steps and not any(is_check(action_of(s) or "") for s in steps):
            problems.append(f"{where}: has steps but no expect_*, http or cmd step, so it proves nothing")
    return problems


def load(path: Path, base_url: str | None = None) -> dict:
    """Read and validate a plan; raises ValueError listing every problem."""
    try:
        plan = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: invalid YAML: {e}") from None
    if problems := validate(plan, base_url):
        raise ValueError(f"{path} has {len(problems)} problem(s):\n" + "\n".join(f"- {p}" for p in problems))
    return plan


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], check=False, capture_output=True, text=True)


def base_plan(path: Path, ref: str) -> dict | None:
    """The plan as committed on `ref`, or None if `ref` has no plan at that path."""
    if git(path.parent, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode:
        raise ValueError(f"'{ref}' is not a commit in this git repository (in CI, fetch the base branch first)")
    shown = git(path.parent, "show", f"{ref}:./{path.name}")
    if shown.returncode:
        return None
    try:
        old = yaml.safe_load(shown.stdout)
    except yaml.YAMLError:
        return None
    return old if isinstance(old, dict) else None


def weakened(plan: dict, base: dict) -> list[str]:
    """Claims and checks that `base` had and `plan` lost. Steps are compared exactly, so an edited
    expectation counts as removed. A claim is skipped when `plan` adds (or rewords) a waiver for it."""
    old_waivers = base.get("waivers") if isinstance(base.get("waivers"), dict) else {}
    waived = {cid for cid, why in (plan.get("waivers") or {}).items() if old_waivers.get(cid) != why}
    now = {c["id"]: c.get("steps") or [] for c in plan["claims"]}
    problems = []
    for c in base.get("claims") or []:
        cid = c.get("id") if isinstance(c, dict) else None
        if not cid or cid in waived:
            continue
        if cid not in now:
            problems.append(f"claim '{cid}' was removed")
            continue
        for step in c.get("steps") or []:
            if is_check(action_of(step) or "") and step not in now[cid]:
                flow = yaml.safe_dump(step, default_flow_style=True, width=1000).strip()
                problems.append(f"claim '{cid}': check removed or changed: `{flow}`")
    return problems
