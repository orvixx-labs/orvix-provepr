# Plan format

A plan lives at `.provepr/plan.yaml` in the project root. It lists the claims a change makes and the browser
steps that prove each one. Write it **before** running, so the expectations aren't shaped by the results.

```yaml
base_url: http://localhost:3000   # required unless passed via --base-url / PROVEPR_BASE_URL
start: npm run dev                # optional: started from the project root, stopped after the run
start_timeout: 30                 # optional: seconds to wait for base_url to answer (default 30)

claims:
  - id: save-persists             # required, unique; used for frame/video file names
    says: "Clicking Save stores the name, and it is still there after a reload"
    steps:                        # optional; a claim without steps is UNVERIFIED
      - goto: /settings
      - fill: { label: Name, value: Ada }
      - click: { role: button, name: Save }
      - expect_text: Saved
      - reload: true
      - expect_value: { label: Name, value: Ada }
```

Each claim runs in a fresh browser context (no shared cookies or storage) at 1280×720. Steps run in order.
The first failing step stops the claim. Every action and expectation times out after 5 seconds.

## Steps

| Step | Argument | Does |
|---|---|---|
| `goto` | path | Opens `base_url` + path |
| `reload` | `true` | Reloads the page |
| `wait` | milliseconds | Sleeps. Prefer an `expect_*` step, which waits only as long as needed |
| `click` | locator | Clicks the element |
| `fill` | locator + `value` | Replaces the input's text |
| `select` | locator + `value` | Picks an `<option>` by value or label |
| `press` | key | Presses a key on the focused element, e.g. `Enter`, `Control+A` |
| `expect_text` | text | The page contains the text |
| `expect_no_text` | text | The page does not contain the text |
| `expect_visible` | locator | The element is visible |
| `expect_value` | locator + `value` | The input's current value equals `value` |
| `expect_url` | text | The URL contains the text |

## Locators

Use the first one that fits. Locators based on role and label survive markup changes, and they also check
that the page is accessible.

| Locator | Matches |
|---|---|
| `{ role: button, name: Save }` | an element by ARIA role and accessible name |
| `{ label: Email }` | a form control by its label |
| `{ placeholder: Search }` | an input by placeholder |
| `{ text: "Sign in" }` | an element by visible text |
| `{ testid: submit }` | `data-testid="submit"` |
| `{ css: "#name" }` | a CSS selector (last resort) |

## Result rules

| Status | When |
|---|---|
| **FAIL** | a step failed, an uncaught page error occurred, a response was HTTP 5xx, or a request failed at the network level (aborted navigations are ignored) |
| **PASS** | all steps passed and none of the above happened |
| **UNVERIFIED** | the claim has no steps |

Console errors and HTTP 4xx responses are listed in the report but do not decide the status. The agent judges them.

## Tips for strong claims

- When a claim is about saved data, check it **after** a `reload` or a navigation. A success message is not proof.
- Write one claim per behaviour, with the `says` text phrased the way it would be in the PR description.
- Do not delete steps or loosen expectations to make a run pass. Fix the code, or report the claim as not holding.
