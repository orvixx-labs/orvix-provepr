# provepr guide for coding agents

This guide ships inside the installed provepr package, so it matches the version you are running.
When it disagrees with what you remember about provepr, this guide is right.

provepr checks the claims a change makes by running them in a real browser, against the app's HTTP
API, and as shell commands. It reports facts: step results, HTTP errors, page and console errors,
command output and screenshots. It never calls an LLM. **You** decide what to check and judge
the evidence; provepr runs it.

## When to use it

After any change a user can see or trigger (pages, forms, buttons, flows, an API behind them, or a
command-line tool), **before** you say the change is done or open a PR.

## The loop

1. **Add claims first.** Before running anything, add one claim per thing your change says it does to
   `.provepr/plan.yaml`. Decide the expectations now, not after seeing results.
2. **Check the plan.** Run `provepr check --base main` (your base branch). Fix every problem it lists.
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
  was removed on purpose; then add a waiver (below) and say so in the PR.
- **`provepr check --base <branch>` enforces this.** It fails when a claim that exists on the base
  branch was removed, or when one of its checks (`expect_*`, `http`, `cmd` steps) was removed or changed.
  Adding claims and steps is always fine. CI runs this check on every PR.
- **FAIL and UNVERIFIED are not done.** List UNVERIFIED claims as unverified. Never call them passing.
- **Do not hand-write CI for provepr.** `provepr init` writes the workflow. If it is missing, run
  `provepr init` instead of writing one.
- Commit `.provepr/plan.yaml`. `.provepr/runs/` (evidence) and `.provepr/baseline/` stay ignored.

## The plan file

```yaml
# .provepr/plan.yaml
base_url: http://localhost:3000   # where the app answers (or pass --base-url / set PROVEPR_BASE_URL)
start: npm run dev                # command that starts the app, run from the project root.
                                  # Required for CI. Omit locally if the app is already running.
start_timeout: 30                 # seconds to wait for base_url to answer (default 30)
parallel: 4                       # optional: run claims on 4 browsers at once (default 1). Only for claims
                                  # that don't depend on each other's data; see "Speed" below.
viewport: desktop                 # optional: desktop (1280×720, default), mobile (390×844 phone), or
                                  # { width: 1024, height: 768 }. A claim can set its own.
setup:                            # optional: steps run ONCE before the claims, e.g. signing in. Every
  - goto: /login                  # claim starts with the cookies and storage the setup left.
  - fill: { label: Email, value: test@example.com }
  - fill: { label: Password, value: "${TEST_PASSWORD}" }   # ${NAME} is read from the environment
  - click: { role: button, name: Sign in }
  - expect_url: /dashboard
before_each:                      # optional: steps run at the start of EVERY claim, e.g. resetting data
  - cmd: { run: npm run db:reset }
waivers:                          # optional: claims you weakened or removed ON PURPOSE, with the reason.
  old-export: "CSV export was removed in this PR"   # only a new or reworded waiver counts

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

  - id: signed-out-redirect
    says: "A visitor who is not signed in is sent to the sign-in page"
    fresh: true                   # skip the setup's sign-in: start with no cookies
    steps:
      - goto: /dashboard
      - expect_url: /login

  - id: email-digest
    says: "A weekly digest email is sent on Mondays"
    # no steps: not checkable here, reported UNVERIFIED (say so honestly)
```

Each claim runs in a fresh browser at 1280×720 (or its `viewport`), starting with only what `setup` left.
Steps run in order, and the first failing step stops the claim. Every action and expectation times out after 5 seconds, but an
expectation stops waiting as soon as the claim has already failed for another reason (an HTTP 5xx, a page
error or a failed request), so a broken save doesn't cost 5 seconds per later step.
A plan whose steps are all `cmd` steps (a CLI tool) needs no `base_url`, and starts no browser.

### Steps

| Step | Argument | Does |
|---|---|---|
| `goto` | path | Opens `base_url` + path |
| `reload` | `true` | Reloads the page |
| `wait` | milliseconds | Sleeps. Prefer an `expect_*` step, which waits only as long as needed |
| `click` | locator | Clicks the element |
| `hover` | locator | Moves the mouse over the element, e.g. to open a tooltip or menu |
| `check` / `uncheck` | locator | Ticks or clears a checkbox or radio button |
| `upload` | locator + `file` | Picks a file (or a list of files) in a file input. Paths are from the project root |
| `fill` | locator + `value` | Replaces the input's text |
| `select` | locator + `value` | Picks an `<option>` by value or label |
| `press` | key | Presses a key, e.g. `Enter`, `Control+A` |
| `expect_text` | text | The page contains the text |
| `expect_no_text` | text | The page does not contain the text |
| `expect_visible` | locator | The element is visible |
| `expect_value` | locator + `value` | The input's current value equals `value` |
| `expect_count` | locator + `count` | Exactly `count` elements match, e.g. `{ css: "li.todo", count: 3 }` |
| `expect_url` | text | The URL contains the text |
| `http` | see below | Calls the app's API and checks the response |
| `mock` | see below | Fakes, fails or delays the page's matching requests, to reach error and loading states |
| `cmd` | see below | Runs a shell command and checks its exit code and output |

### Signing in, clean data and secrets

Most apps need a signed-in user. Put the sign-in in `setup` (see the plan above) instead of repeating it
in every claim: it runs once per run, and every claim starts with the cookies and local storage it left.
It can be browser steps, or an `http` step that calls the login API, or a `cmd` step that creates a test
user. If a setup step fails, the report shows `FAIL _setup` with the failing step (`get_frames("_setup")`
for screenshots), and every claim that depends on it FAILs without running. Mark a claim `fresh: true` to
start it without the setup's state, e.g. to check what signed-out visitors see, or the sign-in page itself.

`before_each` steps run at the start of every claim, after the setup, in the claim's own browser. Use
them to reset or seed data so claims don't depend on each other's order (a `cmd` step running your reset
script, or an `http` call to a test-only reset endpoint). They take no screenshots; if one fails, the
claim FAILs with `before_each step N failed` and its own steps don't run.

**Never write a password, token or API key into the plan: it is committed.** Write `${NAME}` instead and
set the environment variable `NAME` where provepr runs (in CI, a repository secret passed with `env:`).
`${NAME}` works in any step value and in `base_url`; in a `cmd` step's `run`, the shell expands it. A run
stops before starting if a variable the plan uses is not set. Values of 4 or more characters are
replaced with `***` everywhere in `report.md` and `report.json` (errors, notes and command output).
Screenshots are not redacted: password fields show dots, but don't `expect_text` a secret.

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

### The `mock` step

```yaml
- mock: { path: /api/profile, method: POST, status: 500, json: { error: down } }  # answer with this instead
- mock: { path: /api/profile, fail: true }        # the request fails as if the network were down
- mock: { path: /api/search, delay: 2000 }        # the real request, held for 2 s first
- mock: { path: "https://api.stripe.com/**", status: 402 }   # a full URL mocks a third-party call
```

| Key | Means |
|---|---|
| `path` | appended to `base_url` (or a full URL); `*` and `**` are wildcards. Query strings must match, so use `/api/search*` to ignore them |
| `method` | only this method (default: any) |
| `status`, `body`, `json` | answer with this response (status defaults to 200) |
| `fail: true` | fail the request at the network level |
| `delay` | milliseconds to hold the request first; combine with the options above or use alone |

A mock lasts for the rest of the claim, so put it **before** the step that sends the request. It only
affects requests the page makes, not `http` steps. Requests a mock answered or failed don't count
against the app (a faked 500 does not FAIL the claim); a real request that is only delayed still does.
Use it to check the claims a UI makes about failure: "shows an error when saving fails", "shows a
spinner while loading", "does not say Saved when the server rejects the save".

### The `cmd` step

```yaml
- cmd:
    run: mytool add milk --qty 2      # run with the shell, from the project root
    stdin: "y\n"                      # optional input
    expect_exit: 0                    # optional; default 0
    expect_output: "Added 2 milk"     # optional; stdout or stderr must contain this text
    timeout: 30                       # optional; seconds (default 30), then the command is killed
```

Use it for command-line tools, scripts and anything that has no page: a claim can mix `cmd` steps with
browser steps, e.g. seed data with a command, then check the page shows it. Each `cmd` step's frame is a
terminal view of the command and its output, and `report.json` holds the output's last 4000 characters.

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
| **FAIL** | a step failed (for `cmd`: wrong exit code, missing output or timeout), the `setup` or a `before_each` step failed, an uncaught page error occurred, a page request returned HTTP 5xx, or a request failed at the network level (requests a `mock` step answered or failed are left out) |
| **PASS** | every step passed and none of the above happened |
| **UNVERIFIED** | the claim has no steps |

Console errors and HTTP 4xx responses from the page are listed for you to judge but do not decide the status.

`provepr check` rejects plans with unknown steps, bad arguments, duplicate or unsafe ids, a missing `says`,
or a claim whose steps contain no `expect_*`, `http` or `cmd` step (all actions and nothing checked).
With `--base <ref>` it also rejects claims and checks removed since that git ref (see Rules).

## Speed

Most of a run is spent waiting: an expectation that never comes true waits out its 5 seconds. Two things
keep that short:

- **Failed claims stop waiting** (always on, see above).
- **`parallel: N`** runs claims on N browsers at once, so slow claims overlap. A suite of 12 claims took
  3.9 s with `parallel: 4` instead of 6.9 s. Claims share the app and its data, so use it only when claims
  don't read what other claims write (two claims saving different names at once will break each other).
  A FAIL in a parallel run is reported as is, with a hint: if it looks wrong, rerun that claim alone with
  `--only <id>`. If it then passes, the claims clash; turn `parallel` off.

## Visual changes against the base branch

Steps can pass while a page looks broken. To see what your change did to the screens, save a baseline
on the base branch, then compare on yours:

```
git switch main && provepr run --save-baseline     # stores every claim's frames in .provepr/baseline/
git switch -    && provepr run --compare           # marks frames that look different from the baseline
```

A frame is compared only when every step up to it is the same as in the baseline. When more than 0.1%
of its pixels changed, the report says `looks different from the baseline at step N` and writes
`frames/<claim>/NN.diff.png` (your frame, faded, with the changed pixels in red); `get_frames` returns
both. A visual change does not decide PASS or FAIL: you judge whether it was intended. Report unintended
ones as bugs, and mention intended ones in the PR. Dynamic content (times, random data) shows up as changes.

## Writing strong claims

- Phrase `says` the way the PR description would. Write one claim per behaviour.
- When a claim is about saved data, check it **after** a `reload` or navigation. A success message is not proof.
- Check the failure path as well as the happy path: bad input should show an error, not a success message.
  Use `mock` to make the server or network fail and check what the page then says.
- If the change looks different on a phone, add a claim with `viewport: mobile`. A page without
  `<meta name="viewport" content="width=device-width">` renders at desktop width on a phone, as it would
  for real users.
- Prefer checks a user would notice (text, values, URLs, status codes) over implementation details.

## Commands and tools

| CLI | MCP tool | Does |
|---|---|---|
| `provepr init` | | Sets up a repo: this guide's rules in AGENTS.md, `@AGENTS.md` in CLAUDE.md, a CI workflow, a starter plan, .gitignore |
| `provepr docs` | `get_guide` | Prints this guide |
| `provepr check [--base REF]` | | Validates the plan, and that nothing was removed since REF; exits 1 on problems |
| `provepr run [--only ids] [--base-url URL] [--keep-evidence] [--save-baseline] [--compare]` | `run_proof(claims?, base_url?, keep_evidence?, save_baseline?, compare?)` | Runs claims; exits 1 if any FAIL |
| | `get_report(run?)` | The latest (or a named) run's report and contact sheet |
| | `get_frames(claim_id, steps?)` | Screenshots of the failing step and the one before it (or of the steps that look different, with their diffs) |

Each run writes `.provepr/runs/<run>/` containing `report.md`, `report.json`, `contact.png`,
`frames/<claim>/NN.png` (plus `NN.diff.png` with `--compare`), and `app.log` (output of `start`).

To save disk space, a FAILed claim keeps only the screenshot of its failing step and the one before it
(the last step, if it failed for another reason, e.g. an HTTP 500), and any screenshot that looks different
from the baseline is kept. Passing claims keep their report lines and their tile in `contact.png`. No video
is recorded, and only the newest 5 runs are kept. Add `--keep-evidence` (`keep_evidence` in `run_proof`)
to keep every screenshot and record a video per claim (`videos/<claim>.webm`), e.g. for a PR.

## Reporting

End your final message or PR description with:

```
Proof: 3 PASS · 0 FAIL · 1 UNVERIFIED (run 20261018-042535-318161)
- PASS save-persists: Clicking Save stores the name, and it is still there after a reload
- PASS name-too-long-rejected: The API rejects names longer than 100 characters
- UNVERIFIED email-digest: not checkable with provepr
```
