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
uv run pytest                                  # runs the demo app; checks the bug is caught and the fix passes
uv run ruff check . && uv run ruff format .
claude plugin validate .                       # if you touched .claude-plugin/, .codex-plugin/ or .mcp.json
```

## Layout

| Path | What |
|---|---|
| `src/provepr/runner.py` | loads the plan, starts the app, runs claims in Playwright, applies the result rules |
| `src/provepr/report.py` | `report.json`, `report.md`, contact sheet |
| `src/provepr/server.py` | MCP tools (`run_proof`, `get_report`, `get_frames`) |
| `src/provepr/cli.py` | `provepr run`, `provepr mcp` |
| `skills/provepr/SKILL.md` | what the agent is taught |
| `.claude-plugin/`, `.codex-plugin/`, `.mcp.json` | plugin manifests for Claude Code and Codex |
| `examples/demo_app/` | stdlib app with a planted bug, used by the tests |
| `docs/plan-format.md` | plan reference |

## Ground rules

- provepr does not call an LLM. Judgement belongs to the user's agent; provepr reports facts.
- New result rules must be deterministic and covered by a test against the demo app.
- Keep `report.md` short: the agent pays for every token of it.
- If you add a step type, document it in `docs/plan-format.md` and `skills/provepr/SKILL.md`.
