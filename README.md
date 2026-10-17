# orvix-provepr

**Your coding agent says it's done. provepr makes it prove it.**

Before an agent (Claude Code, Codex, or any MCP client) calls a UI change done or opens a PR, it writes
down what the change claims and how a user would check it. provepr runs those checks in a real browser
and hands back facts: which step failed, HTTP 5xx responses, page and console errors, a screenshot after
every step, and a video. The agent fixes what failed and runs the checks again. By the time a human opens
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
With provepr installed, Claude Code found and fixed it by itself (see [What it caught](#what-it-caught)).

## How it works

```
 Coding agent (Claude Code / Codex)
   │ 1. writes the change + .provepr/plan.yaml   (claims + the steps that prove them)
   │ 2. calls run_proof
   ▼
 provepr
   ├─ starts the app (optional `start:` command) and waits for it
   ├─ runs each claim in headless Chromium: video, a screenshot after every step,
   │  console errors, page errors, every HTTP 4xx/5xx and failed request
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
and the skill forbids weakening the plan to make it pass. An agent that grades its own work this way
is much harder to fool, including by its own optimism.

## Install

You need [uv](https://docs.astral.sh/uv/). On first run provepr downloads Chromium for Playwright by itself.

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

### Command line only

```
git clone https://github.com/orvixx-labs/orvix-provepr && cd orvix-provepr && uv sync
uv run provepr run --plan /path/to/your-app/.provepr/plan.yaml
```

## Use it

Ask the agent to prove its change works before it opens a PR, for example:

> Before you open the PR, prove the feature works with provepr and fix anything that's broken.

The bundled skill ([`skills/provepr/SKILL.md`](skills/provepr/SKILL.md)) teaches the agent the plan
format and the loop: plan first, then run, read, fix, run again.

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
```

The full step and locator reference is in [docs/plan-format.md](docs/plan-format.md).

### MCP tools

| Tool | Returns |
|---|---|
| `run_proof(plan_path?, base_url?)` | the short report and one contact sheet image |
| `get_report(plan_path?, run?, include_json?)` | the latest (or a named) run's report, optionally with full JSON |
| `get_frames(claim_id, steps?, plan_path?, run?)` | screenshots of the failing step and the one before it, or of any steps you ask for |

### Command line

```
provepr run [--plan .provepr/plan.yaml] [--base-url URL]   # exits 1 if any claim fails
provepr mcp                                                 # serve the MCP tools over stdio
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

## What it caught

`examples/demo_app` has a planted bug: the save handler reads the wrong JSON key, so every save returns
a 500, and the page shows "Saved" anyway. We gave Claude Code (headless, with this plugin) a fresh copy and
only this prompt:

> I built a profile page where you can type your name and save it. Before I open a PR, prove the feature
> works and fix anything that's broken.

It loaded the skill, wrote its own plan, ran it, and got a FAIL with the HTTP 500. It then fixed the server
**and** the UI that said "Saved" on a failed save, and ran again: `1 PASS · 0 FAIL`. In its final message
it listed the paths it could not check in a browser. That took 14 turns, and provepr made no LLM calls.

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

- Web UIs only for now. Backend-only claims are UNVERIFIED unless a UI exercises them.
- There is no step yet to simulate network failures, so error paths are hard to check.
- No screenshot diff against the base branch yet.
- Runs locally. Hosted runs on PR preview deployments (a GitHub App) are on the roadmap.

## Roadmap

- [x] Local runner, evidence bundle, CLI
- [x] MCP server, skill, Claude Code and Codex plugin
- [ ] Network mocking and failure steps
- [ ] GitHub App: run on every PR against its preview deployment (Vercel, Netlify) and post a PR comment
- [ ] Screenshot diff against the base branch
- [ ] Terminal and CLI apps

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © Orvix Labs
