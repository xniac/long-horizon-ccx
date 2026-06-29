# Working conventions (long-horizon harness)

You are one of several engineers working in shifts on this project. You begin
each session with **no memory of what came before** — the filesystem is your
shared memory. Follow these conventions exactly.

## Always start here
1. Read `PROGRESS.md` (the running log) and `BRIEF.md` (the immutable goal) in full.
2. Read `feature_list.json` — this is the contract. Each feature has `passes`.
3. Run `init.sh` (or the documented smoke test) to confirm the current state.
   **Do not trust the contract — verify it.** A feature marked `passes: true`
   that you cannot reproduce is a bug to fix, not a reason to relax.

## While working
- Work **one feature at a time**. Do not try to one-shot the whole project.
- **Proof before passing.** Only flip a feature to `passes: true` after you have
  produced and *read back* concrete evidence (test output, screenshot, command
  result). The verify-gate enforces this.
- Commit often with descriptive messages. git is your rollback to a known-good
  state.
- Keep notes in `MEMORY.md` short and current — it is capped on purpose.

## Before you stop
- Update `PROGRESS.md`: what you did, what's verified, what remains, any landmines.
- Leave the workspace in a clean, runnable state.
- A completion gate will keep you working while features remain unverified — this
  is intentional. Stop only when the contract is genuinely satisfied (or the step
  budget is hit, or an operator stop is requested).

## Guardrails you will encounter
- **Doom-loop guard**: if you repeat an identical tool call, it is blocked. Do
  not retry with identical arguments — drop a gear and decompose.
- **Reflection checkpoints**: periodically you'll be asked to step back and
  re-check your work against the goal. Answer honestly.
- **Kill switch / steering**: an operator may halt you (`AGENT_STOP`) or inject
  guidance (`STEER.md`). Obey immediately.
