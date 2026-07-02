# Archived real-A/B evidence (Claude Haiku 4.5, `claude -p` CLI backend)

Raw `results.json` + self-contained `dashboard.html` for the three documented
real-model results (DESIGN.md §5). Committed so the claims are inspectable
without re-spending on API calls; a fresh run of the commands in DESIGN.md §7
reproduces them (live runs are billed and non-deterministic).

| dir | repo task | headline result | DESIGN ref |
|---|---|---|---|
| `v05_k3/` | `v05-incremental-app` | success ON 3/3 vs OFF 0/3 | §5.1 |
| `v06_k3/` | `v06-debug-session-scoped` | success ON 3/3 vs OFF 0/3 | §5.2 |
| `v07_k2/` | `v07-debug-amnesiac-pytest` | both pass; ON −45% tokens (−12,652 [−14,380, −10,924]) | §5.3 |

**Naming note**: the task ids *inside* these files carry the experiment-time
names — `v06d-debug-session-scoped` (now repo task `v06`) and
`v06c-debug-amnesiac-pytest` (now repo task `v07`). The task files were renamed
in the final repo cleanup; the task *content* (prompt/verify/run settings) is
what the archived runs executed. `v05-incremental-app` is unchanged.

Each `results.json` contains the full per-trial records (success, partial
credit, tokens, cost, event transcript) plus the paired statistics; each
`dashboard.html` is the zero-dependency rendering of the same file.

Two format notes: the top-level `backend` label is `null` in these files (the
field was added to the report schema after these runs; the grader mode inside
each trial, `executable-checks`, plus the recorded cost identify them as real
CLI runs), and per-trial `partial_credit` is the weighted executable-check
fraction, so e.g. v05 OFF averages 0.308.
