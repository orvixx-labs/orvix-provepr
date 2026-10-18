# orvix-provepr

**Your coding agent says it's done. provepr makes it prove it.**

Before an agent (Claude Code, Codex, Cursor, or anything that reads `AGENTS.md`) calls a change done or
opens a PR, it writes down what the change claims and how to check it. provepr runs those checks in a real
browser and against the app's HTTP API, and hands back facts: which step failed, HTTP 5xx responses, page
and console errors, a screenshot after every step, and a video. The agent fixes what failed and runs the checks again. By the time a human opens
the PR, each claim is marked PASS, FAIL or UNVERIFIED.

provepr never calls an LLM. The agent's own model writes the plan and reads the evidence. provepr only runs
the browser and reports what happened.

```
$ provepr run
# provepr run 20261018-042535-318161
1 PASS · 1 FAIL · 1 UNVERIFIED  (base_url http://127.0.0.1:8765)

## PASS `page-loads`: The profile page loads with a Name field and a Save button
- steps passed: 4/4

## FAIL `save-persists`: Clicking Save stores the name, and it is still there after a reload
- steps passed: 5/6
- FAILED step 6 `expect_value {"label": "Name", "value": "Ada Lovelace"}`: Locator expected to have Value 'Ada Lovelace' | Actual value:
- HTTP 500 POST http://127.0.0.1:8765/api/profile

## UNVERIFIED `name-length-limit`: The API rejects names longer than 100 characters
- no runnable steps (not checkable in a browser yet)
```

In that run the page showed "Saved" while the server returned a 500, so the bug would have shipped.
With provepr set up, Claude Code found and fixed it by itself (see [What it caught](#what-it-caught)).

## Quick start

```
uv tool install git+https://github.com/orvixx-labs/orvix-provepr
cd your-app && provepr init
```

`provepr init` sets the repo up so every agent and CI use provepr the same way:

| File | What it gets |
|---|---|
| `AGENTS.md` | A short managed block telling any agent to read `provepr docs` and follow the loop before calling a change done |
| `CLAUDE.md` | `@AGENTS.md`, so Claude Code reads the same rules |
| `.provepr/plan.yaml` | A starter plan. Set `base_url` and `start` to fit your app |
| `.github/workflows/provepr.yml` | A CI job that runs `provepr check`, then `provepr run`, on every PR and uploads the evidence |
| `.gitignore` | `.provepr/runs/` |

Running it again changes nothing. Existing files are kept, and only the marked AGENTS.md block is refreshed.
After that, just ask your agent for changes as usual.

## How it works

```
 Coding agent (Claude Code / Codex / anything that reads AGENTS.md)
   │ 0. AGENTS.md says: read `provepr docs` (the guide bundled with the installed version)
   │ 1. writes the change + adds claims to .provepr/plan.yaml (the steps that prove them)
   │ 2. runs `provepr check`, then `provepr run` (or the run_proof MCP tool)
   ▼
 provepr
   ├─ starts the app (optional `start:` command) and waits for it
   ├─ runs each claim in headless Chromium: video, a screenshot after every step,
   │  console errors, page errors, every HTTP 4xx/5xx and failed request,
   │  plus `http` steps that call the app's API directly
   ├─ marks each claim PASS / FAIL / UNVERIFIED using fixed rules, no AI
   └─ writes the evidence bundle to .provepr/runs/<run>/
   ▼
 Agent reads a short report (about 250 tokens) and one contact sheet image
   └─ get_frames(claim) for screenshots of the failing step → fixes the code → runs again
```

**What counts as a failure.** A claim FAILs if a step fails, the page throws an uncaught error, a request
returns HTTP 5xx, or a request fails at the network level. Console errors and HTTP 4xx responses are listed
for the agent to judge but do not fail the claim. A claim with no steps is UNVERIFIED, never PASS.

**Why the plan comes first.** The agent decides what "working" looks like before it sees any evidence,
and the guide forbids weakening the plan to make it pass. An agent that grades its own work this way
is much harder to fool, including by its own optimism.

**Why the plan accumulates.** Each change adds claims and keeps the old ones, so `plan.yaml` grows into a
regression suite as a side effect, and CI runs all of it.

**How agents learn the rules.** The guide for coding agents ships inside the package
([`src/provepr/docs/guide.md`](src/provepr/docs/guide.md)), so it always matches the installed version.
Agents reach it through `provepr docs`, the `get_guide` MCP tool, or the plugin's skill. All three point to
the same file. `provepr check` enforces the parts a guide can't: unknown steps, unsafe ids, and claims
with no checks are rejected before anything runs.

## Install

You need [uv](https://docs.astral.sh/uv/). On first run provepr downloads Chromium for Playwright by itself.

> The repository is private while in early access. The install commands below need access to it.

### Any agent (AGENTS.md)

```
uv tool install git+https://github.com/orvixx-labs/orvix-provepr
provepr init
```

This works with any agent that reads `AGENTS.md` and can run shell commands.

### Claude Code

```
/plugin marketplace add orvixx-labs/orvix-provepr
/plugin install provepr@orvix
```

To load it for a single session from a local checkout: `claude --plugin-dir /path/to/orvix-provepr`.

### Codex

```
codex plugin marketplace add orvixx-labs/orvix-provepr
codex plugin add provepr
```

Or add it as a plain MCP server:

```
git clone https://github.com/orvixx-labs/orvix-provepr
codex mcp add provepr -- uv run --quiet --project /path/to/orvix-provepr provepr mcp
```

> Codex support has not been tested end to end yet. Claude Code has (see below). Please open an issue if it doesn't work for you.

## Use it

Ask the agent to prove its change works before it opens a PR, for example:

> Before you open the PR, prove the feature works with provepr and fix anything that's broken.

With `provepr init` done or the plugin installed, the agent learns the plan format and the loop from the
bundled guide: claims first, then check, run, read, fix, and run again.

### Writing a plan

```yaml
# .provepr/plan.yaml  (commit this; ignore .provepr/runs/)
base_url: http://localhost:3000
start: npm run dev          # optional; run from the project root, stopped after the run

claims:
  - id: save-persists
    says: "Clicking Save stores the name, and it is still there after a reload"
    steps:
      - goto: /settings
      - fill: { label: Name, value: Ada }
      - click: { role: button, name: Save }
      - expect_text: Saved
      - reload: true
      - expect_value: { label: Name, value: Ada }

  - id: name-too-long-rejected
    says: "The API rejects names longer than 100 characters"
    steps:
      - http: { method: POST, path: /api/profile, json: { name: "..." }, expect_status: 400 }
```

The full step and locator reference is in the guide: run `provepr docs`, or read
[`src/provepr/docs/guide.md`](src/provepr/docs/guide.md).

### MCP tools

| Tool | Returns |
|---|---|
| `get_guide()` | the guide for coding agents (plan format, steps, rules) |
| `run_proof(plan_path?, base_url?, claims?)` | the short report and one contact sheet image |
| `get_report(plan_path?, run?, include_json?)` | the latest (or a named) run's report, optionally with full JSON |
| `get_frames(claim_id, steps?, plan_path?, run?)` | screenshots of the failing step and the one before it, or of any steps you ask for |

### Command line

```
provepr init [dir] [--no-ci]                  # set up AGENTS.md, CLAUDE.md, plan, CI workflow, .gitignore
provepr docs                                  # print the guide for coding agents
provepr check [--plan PATH]                   # validate the plan; exits 1 on problems
provepr run [--plan PATH] [--base-url URL] [--only id1,id2]   # exits 1 if any claim fails
provepr mcp                                   # serve the MCP tools over stdio
```

`base_url` resolves in this order: `--base-url`, then the `PROVEPR_BASE_URL` environment variable, then the plan.

### The evidence bundle

```
.provepr/runs/20261018-042535-318161/
├── report.md          what the agent reads
├── report.json        every step, error and timing
├── contact.png        one frame per claim, captioned PASS/FAIL
├── frames/<claim>/NN.png
├── videos/<claim>.webm
└── app.log            output of the `start` command
```

## CI

`provepr init` writes `.github/workflows/provepr.yml`. It installs Chromium, runs `provepr check`, runs every
claim, and uploads `.provepr/runs/` as a build artifact even when the job fails. The `start:` command in the
plan boots your app on the CI runner, so add the steps that install your app's dependencies where the
workflow's comment shows. Don't hand-write this file: `init` produces the same tested template everywhere.

## What it caught

`examples/demo_app` has a planted bug: the save handler reads the wrong JSON key, so every save returns
a 500, and the page shows "Saved" anyway. We gave Claude Code (headless, with this plugin) a fresh copy and
only this prompt:

> I built a profile page where you can type your name and save it. Before I open a PR, prove the feature
> works and fix anything that's broken.

It loaded the skill, wrote its own plan, ran it, and got a FAIL with the HTTP 500. It then fixed the server
**and** the UI that said "Saved" on a failed save, and ran again: `1 PASS · 0 FAIL`. In its final message
it listed the paths it could not check in a browser. That took 14 turns, and provepr made no LLM calls.

We repeated the test with **no plugin at all**, only the files `provepr init` writes, and a prompt that
never mentions provepr ("make sure it works and fix anything that's broken"). Claude Code read the rules
from AGENTS.md and ran `provepr docs`. It added two claims while keeping the existing one, used an `http`
step to check that bad input gets a 400, fixed both bugs, and finished at `3 PASS · 0 FAIL` in 13 turns.

Run the demo yourself:

```
cd examples/demo_app && uv run --project ../.. provepr run
```

## How it differs from other tools

- **Screen recorders for agents** capture what the agent did so a human can review it later. provepr checks
  claims the agent committed to beforehand and returns a pass/fail result the agent acts on.
- **Visual regression tools** (screenshot diffing) tell you pixels changed, not whether the feature works.
- **AI code reviewers** read the diff. provepr runs the change.

## Limits

- Web apps only for now: browser steps plus `http` steps against the app's API. CLI tools and
  background jobs are UNVERIFIED.
- There is no step yet to simulate network failures in the browser, so a UI's own error messages are
  hard to reach.
- No screenshot diff against the base branch yet.
- Runs locally. Hosted runs on PR preview deployments (a GitHub App) are on the roadmap.

## Roadmap

- [x] Local runner, evidence bundle, CLI
- [x] MCP server, skill, Claude Code and Codex plugin
- [x] Bundled agent guide, `provepr init` (AGENTS.md, CI workflow), `provepr check`
- [x] Accumulating plans, `--only`, `http` steps for API claims
- [ ] Network mocking and failure steps in the browser
- [ ] `provepr check --base main`: flag claims removed or weakened compared to the base branch
- [ ] GitHub App: run on every PR against its preview deployment (Vercel, Netlify) and post a PR comment
- [ ] Screenshot diff against the base branch
- [ ] Terminal and CLI apps

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © Orvix Labs
