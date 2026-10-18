# provepr guide for coding agents

This guide ships inside the installed provepr package, so it matches the version you are running.
When it disagrees with what you remember about provepr, this guide is right.

provepr checks the claims a change makes by running them in a real browser (and against the app's
HTTP API). It reports facts: step results, HTTP errors, page and console errors, screenshots and video.
It never calls an LLM. **You** decide what to check and judge the evidence; provepr runs it.

## When to use it

After any change a user can see or trigger (pages, forms, buttons, flows, or an API behind them),
**before** you say the change is done or open a PR.

## The loop

1. **Add claims first.** Before running anything, add one claim per thing your change says it does to
   `.provepr/plan.yaml`. Decide the expectations now, not after seeing results.
2. **Check the plan.** Run `provepr check`. Fix every problem it lists.
3. **Run.** Use `provepr run` (or the `run_proof` MCP tool). While iterating on your own claims, add
   `--only id1,id2` (`claims` in the tool) for speed; run everything once before you finish.
4. **Read the evidence.** For each FAIL, look at the failing step, the HTTP and console errors, and the
   screenshots (`get_frames` MCP tool, or `.provepr/runs/<run>/frames/<claim>/`). Also look at the
   contact sheet's PASS frames: a page that passed but is visibly broken is still broken.
5. **Fix the code, not the plan,** and run again. Repeat until nothing FAILs.
6. **Report** the result in your final message or PR description (format at the end of this guide).

## Rules

- **Never weaken the plan to make it pass.** Do not delete steps, loosen expectations, or remove claims.
  If a claim turns out to be wrong, fix the code, or state plainly that the claim does not hold.
- **The plan accumulates.** Add claims for your change and keep the existing ones: they are this
  project's regression suite and CI runs all of them. Remove a claim only when the feature it checks
  was removed on purpose, and say so in the PR.
- **FAIL and UNVERIFIED are not done.** List UNVERIFIED claims as unverified. Never call them passing.
- **Do not hand-write CI for provepr.** `provepr init` writes the workflow. If it is missing, run
  `provepr init` instead of writing one.
- Commit `.provepr/plan.yaml`. `.provepr/runs/` is evidence output and stays ignored.

## The plan file

```yaml
# .provepr/plan.yaml
base_url: http://localhost:3000   # where the app answers (or pass --base-url / set PROVEPR_BASE_URL)
start: npm run dev                # command that starts the app, run from the project root.
                                  # Required for CI. Omit locally if the app is already running.
start_timeout: 30                 # seconds to wait for base_url to answer (default 30)

claims:
  - id: save-persists             # letters, digits, - and _; unique; used in file names
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
      - http: { method: POST, path: /api/profile, json: { name: "xxxxxxxxxx...101 chars" }, expect_status: 400 }

  - id: email-digest
    says: "A weekly digest email is sent on Mondays"
    # no steps: not checkable here, reported UNVERIFIED (say so honestly)
```

Each claim runs in a fresh browser (no shared cookies or storage) at 1280×720. Steps run in order, and
the first failing step stops the claim. Every action and expectation times out after 5 seconds.

### Steps

| Step | Argument | Does |
|---|---|---|
| `goto` | path | Opens `base_url` + path |
| `reload` | `true` | Reloads the page |
| `wait` | milliseconds | Sleeps. Prefer an `expect_*` step, which waits only as long as needed |
| `click` | locator | Clicks the element |
| `fill` | locator + `value` | Replaces the input's text |
| `select` | locator + `value` | Picks an `<option>` by value or label |
| `press` | key | Presses a key, e.g. `Enter`, `Control+A` |
| `expect_text` | text | The page contains the text |
| `expect_no_text` | text | The page does not contain the text |
| `expect_visible` | locator | The element is visible |
| `expect_value` | locator + `value` | The input's current value equals `value` |
| `expect_url` | text | The URL contains the text |
| `http` | see below | Calls the app's API and checks the response |

### The `http` step

```yaml
- http:
    method: POST                  # default GET
    path: /api/profile            # appended to base_url
    json: { name: Ada }           # optional request body, sent as JSON
    headers: { X-Token: abc }     # optional
    expect_status: 201            # optional; without it any 2xx/3xx passes and 4xx/5xx fails
    expect_body: '"name":"Ada"'   # optional; the response body must contain this text
```

The request uses the same browser session as the claim's earlier steps, so it carries any cookies a
login step set. Use it for backend claims (validation, status codes, saved data) and to check error
paths, e.g. `expect_status: 400` for bad input.

### Locators

Use the first that fits. Role and label locators survive markup changes, and they also check accessibility.

| Locator | Matches |
|---|---|
| `{ role: button, name: Save }` | an element by ARIA role and accessible name |
| `{ label: Email }` | a form control by its label |
| `{ placeholder: Search }` | an input by its placeholder |
| `{ text: "Sign in" }` | an element by visible text |
| `{ testid: submit }` | `data-testid="submit"` |
| `{ css: "#name" }` | a CSS selector (last resort) |

## What decides PASS / FAIL

| Status | When |
|---|---|
| **FAIL** | a step failed, an uncaught page error occurred, a page request returned HTTP 5xx, or a request failed at the network level |
| **PASS** | every step passed and none of the above happened |
| **UNVERIFIED** | the claim has no steps |

Console errors and HTTP 4xx responses from the page are listed for you to judge but do not decide the status.

`provepr check` rejects plans with unknown steps, bad arguments, duplicate or unsafe ids, a missing `says`,
or a claim whose steps contain no `expect_*` or `http` step (all actions and nothing checked).

## Writing strong claims

- Phrase `says` the way the PR description would. Write one claim per behaviour.
- When a claim is about saved data, check it **after** a `reload` or navigation. A success message is not proof.
- Check the failure path as well as the happy path: bad input should show an error, not a success message.
- Prefer checks a user would notice (text, values, URLs, status codes) over implementation details.

## Commands and tools

| CLI | MCP tool | Does |
|---|---|---|
| `provepr init` | | Sets up a repo: this guide's rules in AGENTS.md, `@AGENTS.md` in CLAUDE.md, a CI workflow, a starter plan, .gitignore |
| `provepr docs` | `get_guide` | Prints this guide |
| `provepr check` | | Validates the plan; exits 1 on problems |
| `provepr run [--only ids] [--base-url URL] [--keep-evidence]` | `run_proof(claims?, base_url?, keep_evidence?)` | Runs claims; exits 1 if any FAIL |
| | `get_report(run?)` | The latest (or a named) run's report and contact sheet |
| | `get_frames(claim_id, steps?)` | Screenshots of the failing step and the one before it |

Each run writes `.provepr/runs/<run>/` containing `report.md`, `report.json`, `contact.png`,
`frames/<claim>/NN.png`, `videos/<claim>.webm`, and `app.log` (output of `start`).

To save disk space, screenshots and video are kept only for claims that FAILed. Passing claims keep their
report lines and their frame in `contact.png`. Only the newest 5 runs are kept. Add `--keep-evidence`
(`keep_evidence` in `run_proof`) when someone needs the full recordings, e.g. a video for a PR.

## Reporting

End your final message or PR description with:

```
Proof: 3 PASS · 0 FAIL · 1 UNVERIFIED (run 20261018-042535-318161)
- PASS save-persists: Clicking Save stores the name, and it is still there after a reload
- PASS name-too-long-rejected: The API rejects names longer than 100 characters
- UNVERIFIED email-digest: not checkable with provepr
```
