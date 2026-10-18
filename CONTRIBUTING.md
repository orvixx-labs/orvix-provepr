# Contributing

Issues and pull requests are welcome. Bug reports are most useful with the plan, `report.md`, and
the contact sheet from the failing run.

## Setup

```
uv sync
uv run playwright install chromium
```

## Before opening a PR

```
uv run pytest                                  # demo app (bug caught, fix passes), http steps, check, init
uv run ruff check . && uv run ruff format .
claude plugin validate .                       # if you touched .claude-plugin/, .codex-plugin/ or .mcp.json
```

## Releasing

Releases go to PyPI as [`orvix-provepr`](https://pypi.org/project/orvix-provepr/) through
`.github/workflows/publish.yml` (PyPI Trusted Publishing, so no API token is stored anywhere).

1. Bump `version` in `pyproject.toml` and in `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`.
2. Commit, then create a GitHub release with a matching tag, e.g. `v0.2.0`.
3. The workflow runs the tests, builds, and publishes.

## Layout

| Path | What |
|---|---|
| `src/provepr/plan.py` | loads and validates the plan (`provepr check`, and before every run) |
| `src/provepr/runner.py` | starts the app, runs claims in Playwright (browser and `http` steps), applies the result rules |
| `src/provepr/report.py` | `report.json`, `report.md`, contact sheet |
| `src/provepr/server.py` | MCP tools (`get_guide`, `run_proof`, `get_report`, `get_frames`) |
| `src/provepr/cli.py` | `provepr init`, `docs`, `check`, `run`, `mcp` |
| `src/provepr/init.py` | the AGENTS.md block, CLAUDE.md import, CI workflow and starter plan templates |
| `src/provepr/docs/guide.md` | **the** guide for coding agents; everything else points here |
| `skills/provepr/SKILL.md` | thin pointer to the guide, for plugin installs |
| `.claude-plugin/`, `.codex-plugin/`, `.mcp.json` | plugin manifests for Claude Code and Codex |
| `examples/demo_app/` | stdlib app with a planted bug, used by the tests |

## Ground rules

- provepr does not call an LLM. Judgement belongs to the user's agent; provepr reports facts.
- New result rules must be deterministic and covered by a test against the demo app.
- Keep `report.md` short: the agent pays for every token of it.
- If you add a step type, add it to `STEPS` in `plan.py`, implement it in `runner.do_step`, and document it in
  `src/provepr/docs/guide.md`. Do not copy guide content into the skill, README or AGENTS block: link to it.
