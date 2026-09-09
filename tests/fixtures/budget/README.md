# Budget observation fixtures (`refine_routine` p1 step 3)

`ag.budget.v1` documents as the agentroom relay's `/budget` answers them,
frozen at a "now" of 2026-09-09T15:00:00Z (epoch 1788966000). Each is one
observation a routine run may face; `agbudget --source <file>` renders it,
and `AGFRONT_BUDGET_URL=<file>` places it in front of a live run.

- `below.json` — the 5-hour session at 31 %: a "until 50 %" condition is not reached.
- `reached.json` — the session at 57 %: the condition is met before any work starts.
- `reset.json` — the session read at 72 % fifteen minutes ago, but its reset time passed ten minutes ago: the number shown is from a window that is over.
- `failed.json` — the read failed (expired token); the last good numbers (12 %, three hours old) are stale.
- `agy.json` — the pool-matching case (`runtime-profile` step4): `agy` (pool
  `antigravity`) at 71 %, so "until agy's usage exceeds 70 %" is already
  reached; `gemini_cli` (pool `google`) could not be read, so a condition on
  *that* pool is unknown rather than 0; and `agcode`, whose account follows
  its model, is rendered as `pool unknown` and can be matched to no option
  at all.
