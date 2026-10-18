"""Load and validate .provepr/plan.yaml. Used by `provepr check` and before every run."""

import os
import re
from pathlib import Path

import yaml

LOCATOR_KEYS = ("role", "label", "text", "placeholder", "testid", "css")
HTTP_KEYS = {"method", "path", "json", "headers", "expect_status", "expect_body"}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")  # ids become file names

# step -> argument kind
STEPS = {
    "goto": "text",
    "reload": "any",
    "wait": "int",
    "press": "text",
    "click": "locator",
    "expect_visible": "locator",
    "fill": "locator+value",
    "select": "locator+value",
    "expect_value": "locator+value",
    "expect_text": "text",
    "expect_no_text": "text",
    "expect_url": "text",
    "http": "http",
}


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
    if kind == "http":
        if not isinstance(arg, dict) or "path" not in arg:
            return f"'http' needs at least a 'path', got {arg!r}"
        if extra := set(arg) - HTTP_KEYS:
            return f"'http' has unknown keys {sorted(extra)}; allowed: {sorted(HTTP_KEYS)}"
    return None


def validate(plan, base_url: str | None = None) -> list[str]:
    """Every problem in the plan, as human-readable lines. Empty means valid."""
    if not isinstance(plan, dict):
        return ["the plan must be a YAML mapping with 'base_url' and 'claims'"]
    problems = []
    if not (base_url or plan.get("base_url") or os.environ.get("PROVEPR_BASE_URL")):
        problems.append("missing 'base_url' (or pass --base-url, or set PROVEPR_BASE_URL)")
    claims = plan.get("claims")
    if not isinstance(claims, list) or not claims:
        return problems + ["'claims' must be a non-empty list"]
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
        steps = c.get("steps") or []
        if not isinstance(steps, list):
            problems.append(f"{where}: 'steps' must be a list")
            continue
        for n, step in enumerate(steps, 1):
            if p := step_problem(step):
                problems.append(f"{where} step {n}: {p}")
        actions = [next(iter(s)) for s in steps if isinstance(s, dict) and s]
        if steps and not any(a.startswith("expect_") or a == "http" for a in actions):
            problems.append(f"{where}: has steps but no expect_* or http step, so it proves nothing")
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
