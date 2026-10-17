---
name: provepr
description: Prove a web UI change actually works before claiming it is done or opening a PR. Use after changing anything a user can see or click (pages, forms, buttons, flows, API calls behind them). Writes .provepr/plan.yaml, runs it in a real browser via the provepr MCP tools, and fixes what fails.
---

# provepr: prove your change works in a real browser

Tests passing and "it compiles" are not proof that a feature works. Before you say a UI change is done,
run it in a browser and look at the evidence. The `provepr` MCP tools run the browser and return facts
(step results, HTTP 4xx/5xx, console and page errors, screenshots). You judge whether each claim holds.

## 1. Write the plan first

Create `.provepr/plan.yaml` at the project root **before running anything**. One claim per thing your
change says it does, phrased the way a PR description would say it. The steps are what a user would
do, and the `expect_*` steps are what must be true if the claim holds. Decide the expectations now,
not after seeing the results.

```yaml
base_url: http://localhost:3000
start: npm run dev            # optional: command that starts the app (run from project root)
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
  - id: rate-limit
    says: "The API rejects more than 10 requests a minute"
    # no steps -> reported UNVERIFIED (say so honestly in the PR)
```

Steps: `goto: /path`, `click: <loc>`, `fill: <loc> + value`, `select: <loc> + value`, `press: Enter`,
`reload: true`, `wait: 500` (ms), `expect_text: "..."`, `expect_no_text: "..."`,
`expect_visible: <loc>`, `expect_value: <loc> + value`, `expect_url: /path`.

Locators (`<loc>`), in order of preference: `{ role: button, name: Save }`, `{ label: Email }`,
`{ placeholder: Search }`, `{ text: "Sign in" }`, `{ testid: submit }`, `{ css: "#id" }`.

Always end a claim with an `expect_*` step that checks the outcome **after** a reload or navigation
when the claim is about saving data. A success toast is not proof the data was saved.

## 2. Run it

Call `run_proof` (defaults to `.provepr/plan.yaml`). If the app is already running and the plan has no
`start`, pass `base_url`. You get a short report and one contact sheet image.

A claim FAILS on a failed step, an uncaught page error, an HTTP 5xx, or a failed request. Console
errors and HTTP 4xx are listed for you to judge. A claim with no steps is UNVERIFIED.

## 3. Read the evidence and fix

- For a FAIL, call `get_frames(claim_id)` to see the screen at the failing step and the one before.
- Find the root cause in the code (the report points at the request, the step, and the error), fix it,
  and call `run_proof` again. Repeat until every claim with steps passes.
- Also look at PASS claims' screenshots in the contact sheet: a pass with a visibly broken page is
  still broken.

## Rules

- **Never weaken the plan to make it pass** (deleting steps, loosening expectations, removing claims).
  If a claim was wrong, fix the code or say plainly in your answer/PR that the claim does not hold.
- Do not mark a claim done if it is FAIL or UNVERIFIED. List UNVERIFIED claims as unverified.
- Commit `.provepr/plan.yaml`; add `.provepr/runs/` to `.gitignore`.

## 4. Report

When everything passes, include this in your final message or PR description:

```
Proof: N PASS · 0 FAIL · M UNVERIFIED (run <run id>)
- PASS save-persists: Clicking Save stores the name, and it is still there after a reload
- UNVERIFIED rate-limit: not checkable in a browser
```
