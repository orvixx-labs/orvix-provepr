---
name: provepr
description: Prove a change a user can see or trigger (pages, forms, buttons, flows, the API behind them, or a command-line tool) actually works before claiming it is done or opening a PR. Adds claims to .provepr/plan.yaml, runs them in a real browser (or as shell commands) with the provepr MCP tools, and fixes what fails.
---

# provepr

1. Call the `get_guide` tool and follow it. It is the source of truth for the plan format, the step
   types and the rules, and it matches the installed version.
2. Add claims for your change to `.provepr/plan.yaml` **before** running. Keep existing claims.
3. Call `run_proof`, read the report, call `get_frames` for failures, fix the code, and run again
   until nothing FAILs.
4. Never delete claims or loosen expectations to make a run pass. Report FAIL and UNVERIFIED honestly.
