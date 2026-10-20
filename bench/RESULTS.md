# provepr vs Playwright MCP vs Claude in Chrome

Same task, same prompt, same model; only the browser tool differs. 2 runs per tool, each followed by a
rerun. Numbers are the mean of the 2 runs, with both runs in brackets. Raw data: [`results.jsonl`](results.jsonl).

## The task

A small profile page ([`examples/demo_app`](../examples/demo_app)) with two planted bugs:

1. **Save is broken.** The server reads the wrong JSON key, so every save returns HTTP 500.
2. **The UI lies.** The page shows "Saved" without checking the response, so the 500 is invisible.

Every run got the same prompt, naming only that run's tool:

> I built a profile page where you can type your name and save it. Start it with `python3 server.py`; it
> serves http://127.0.0.1:8765/. Before I open a PR, prove the feature works in a real browser and fix
> anything that's broken. Use {provepr | the Playwright browser tools | Claude in Chrome} for the browser checks.

Afterwards a **hidden grader** the agent never saw checked the code it left behind: a saved name survives a
reload (bug 1), and the page does not say "Saved" when the server rejects the save (bug 2). Then the same
session was asked to **verify again** ("the rerun"), which is what every later change costs.

## Results

| | **provepr** | Playwright MCP | Claude in Chrome |
|---|---|---|---|
| Bug 1 fixed (save persists) | ✅ 2/2 | ✅ 2/2 | ✅ 2/2 |
| Bug 2 fixed (no false "Saved") | ✅ 2/2 | ✅ 2/2 | ✅ 2/2 |
| **Failure path tested in the browser** | **✅ 2/2** (a `mock` claim fakes the 500) | ❌ 0/2 (fixed by reading code) | ❌ 0/2 (fixed by reading code) |
| Tool calls to the browser tool | 4 (1 guide + 3 runs) | 19.5 (18, 21) | 29 (33, 25) |
| Turns | **12** (12, 12) | 18 (19, 17) | 30 (30, 30) |
| Wall time | 42 s (40, 43) | 43 s (52, 34) | 129 s (143, 115) |
| Tokens processed | **170k** (173k, 167k) | 193k (239k, 147k) | 374k (364k, 384k) |
| Output tokens | **2.1k** (2.1k, 2.0k) | 2.3k (2.5k, 2.1k) | 4.3k (4.5k, 4.1k) |
| Cost (list price) | $0.115 ($0.116, $0.114) | **$0.095** ($0.102, $0.087) | $0.228 ($0.254, $0.202) |
| **Rerun**: turns | **2** (2, 2) | 11 (10, 12) | 14 (17, 11) |
| **Rerun**: time | **15 s** (18, 11) | 27 s (33, 20) | 60 s (76, 43) |
| **Rerun**: what was re-checked | all 3 claims, failure path included | happy path only | happy path only |
| **Rerun**: cost | $0.132 ($0.132, $0.131) | **$0.127** ($0.134, $0.120) | $0.280 ($0.314, $0.246) |
| Left behind for CI and the next change | `.provepr/plan.yaml` with 3 claims | nothing | nothing |

## What this shows

- **Everyone fixed both bugs** on this small task. provepr's edge here is not catching more, but *what* gets
  proven and what is left afterwards.
- **Only provepr exercised the failure path.** In all four Playwright and Chrome runs the agent fixed the
  "Saved" bug by reading the code, then checked only the happy path in the browser. A fix that was never run
  is a claim, not a proof.
- **The behaviour is steady.** provepr took 12 turns and wrote the same 3 claims in both runs. Playwright
  MCP's cost and time varied more (147k–239k tokens, 34–52 s).
- **Reruns take fewer steps.** provepr re-checks everything, failure path included, in 2 turns and about
  15 s. Driving the browser repeats every click: 11 turns (Playwright) and 14 (Chrome), to re-check less.
- **Cost: Playwright MCP is slightly cheaper; Claude in Chrome costs about 2×.** Playwright MCP was the cheapest
  on both first runs (about $0.02 less) and both reruns cost about the same. Resuming a session re-reads its whole
  history, so most rerun tokens are cache reads, not browser work.
- **The plan stays.** provepr's run ends with three committed claims that CI and every later change re-run with
  no LLM at all. The other two leave a chat transcript.

## Caveats

- **2 runs per tool.** Enough to see the pattern and how much it varies, not enough for fine-grained numbers.
- **One small app.** Bigger apps with more steps per check should widen the gap in browser calls and turns;
  that is not measured here.
- **One model:** `claude-sonnet-5-5` in Claude Code 2.1.303, headless (`claude -p`). Costs are Claude Code's
  list-price figures (`total_cost_usd`).
- **Tokens processed** = input + cache writes + cache reads + output. Cache reads are cheap and dominate.
- **"Failure path tested"** comes from the session transcripts: no Playwright or Chrome run made a save fail
  (for example by mocking the request or breaking the server) before checking the message.
- **Claude in Chrome** drives a real, visible Chrome through the extension. Playwright MCP 0.0.83 and provepr
  run headless Chromium.
- Each run was isolated: no user settings, plugins or MCP servers except that run's tool
  (`--setting-sources project --strict-mcp-config`), a fresh copy of the app, and no `.provepr/` folder or
  "planted bug" hint in it.

## Reproduce

```
npm install @playwright/mcp@0.0.83          # for the Playwright arm
PW_MCP_CLI=$PWD/node_modules/@playwright/mcp/cli.js \
  uv run python bench/run.py --arms provepr,playwright,chrome --reps 2 --model sonnet
```

Needs a logged-in `claude`, and for the Chrome arm the Claude in Chrome extension. The harness is
[`bench/run.py`](run.py); the hidden grader is the `GRADER` plan inside it.
