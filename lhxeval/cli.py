"""Command-line entry point for the eval harness.

    lhx-eval run      --tasks lhxeval/tasks/synthetic -k 5 --backend simulated
    lhx-eval validate --tasks lhxeval/tasks/synthetic     # reference-solution sanity check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .backends import BackendError, get_backend
from .graders import grade_outcome, reference_solution_outcome
from .runner import run_ab
from .report import write_dashboard, write_results_json
from .tasks.schema import load_suite

DEFAULT_TASKS = Path(__file__).parent / "tasks" / "synthetic"


def cmd_validate(args: argparse.Namespace) -> int:
    """Prove every task is solvable and graders are not vacuous.

    For each task: the reference-solution outcome must grade success=True, and an
    empty outcome must grade success=False. A task failing either is broken.
    """
    from .backends import RunOutcome

    tasks = load_suite(Path(args.tasks))
    ok = True
    for t in tasks:
        ref = grade_outcome(t, reference_solution_outcome(t))
        empty = grade_outcome(t, RunOutcome())
        status = "ok"
        if not ref.success:
            status, ok = "BROKEN: reference does not pass", False
        elif empty.success:
            status, ok = "BROKEN: empty outcome passes (vacuous grader)", False
        print(f"  [{t.id}] {t.n_features} features — {status}")
    print(f"\n{'ALL TASKS VALID' if ok else 'VALIDATION FAILED'} ({len(tasks)} tasks)")
    return 0 if ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    tasks = load_suite(Path(args.tasks))
    if args.difficulty:
        tasks = [t for t in tasks if t.difficulty == args.difficulty]
    if args.verified_only:
        # Only tasks with executable `verify` checks — for a REAL A/B on a real
        # backend graded by actual tests, not the agent's self-report.
        tasks = [t for t in tasks if t.verify]
    if args.task_id:
        wanted = set(args.task_id)
        tasks = [t for t in tasks if t.id in wanted]
    if not tasks:
        print(
            f"WARNING: no tasks match filter "
            f"(difficulty={args.difficulty!r}, task_id={args.task_id!r}, "
            f"verified_only={args.verified_only}) in {args.tasks}. Nothing to run.",
            file=sys.stderr,
        )
        return 1
    backend = get_backend(args.backend)
    # Show the resolved transport too: `backend.name` is "claude-sdk" whether the
    # underlying transport is `claude -p` (cli) or the Python SDK, which is easy to
    # misread as "always the SDK".
    label = backend.name
    resolver = getattr(backend, "_resolve_transport", None)
    if resolver is not None:
        try:
            label += f", transport={resolver()}"
        except Exception:
            pass  # no transport available yet; backend.run surfaces it properly
    print(f"Running A/B: {len(tasks)} tasks x k={args.k} x 2 arms "
          f"= {len(tasks) * args.k * 2} trials (backend={label})")

    def _progress(done, total, task_id, arm):
        bar_w = 24
        filled = int(bar_w * (done - 1) / total)
        bar = "█" * filled + "·" * (bar_w - filled)
        print(f"\r  [{bar}] {done}/{total}  {task_id} ({arm})        ",
              end="", file=sys.stderr, flush=True)
        if done == total:
            print(file=sys.stderr)

    try:
        result = run_ab(tasks, backend=backend, k=args.k, base_seed=args.seed,
                        progress=_progress)
    except BackendError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        print("No results written — a backend failure is not a task failure, so "
              "it must not be scored as 0% pass.", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    write_results_json(result, out_dir / "results.json")
    write_dashboard(result, out_dir / "dashboard.html")

    on, off = result.on, result.off
    print("\n=== Headline ===")
    print(f"  pass@1     ON {on.pass_at_1:.3f}  vs  OFF {off.pass_at_1:.3f}")
    print(f"  pass^{on.reliability_k}      ON {on.pass_caret_k:.3f}  vs  OFF {off.pass_caret_k:.3f}")
    print(f"  compaction-survival  ON {_p(on.compaction_survival)}  vs  OFF {_p(off.compaction_survival)}")
    print(f"  resume-after-intr.   ON {_p(on.resume_success)}  vs  OFF {_p(off.resume_success)}")
    print(f"  drift rate ON {on.drift_rate:.3f}  vs  OFF {off.drift_rate:.3f}")
    print(f"  doom/trial ON {on.doom_loop_rate:.3f}  vs  OFF {off.doom_loop_rate:.3f}")
    print(f"\n  success delta (paired): {result.success_ci}")
    print(f"  {result.mcnemar}")
    print(f"\nWrote {out_dir / 'results.json'} and {out_dir / 'dashboard.html'}")
    return 0


def _p(x) -> str:
    return "—" if x is None else f"{x:.3f}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lhx-eval", description="Long-horizon module A/B eval harness")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="run the A/B eval and write the dashboard")
    pr.add_argument("--tasks", default=str(DEFAULT_TASKS))
    pr.add_argument("-k", type=int, default=5, help="trials (seeds) per task per arm")
    pr.add_argument("--seed", type=int, default=1000, help="base seed")
    pr.add_argument("--backend", default="simulated", help="simulated | sdk")
    pr.add_argument("--difficulty", default=None, help="capability | regression")
    pr.add_argument("--verified-only", action="store_true",
                    help="only tasks with executable `verify` checks (real-graded A/B)")
    pr.add_argument("--task-id", action="append", default=None,
                    help="filter to a specific task id (repeatable)")
    pr.add_argument("--out", default="runs/latest")
    pr.set_defaults(func=cmd_run)

    pv = sub.add_parser("validate", help="reference-solution sanity check on the task suite")
    pv.add_argument("--tasks", default=str(DEFAULT_TASKS))
    pv.set_defaults(func=cmd_validate)

    # Load .env so `--backend sdk` picks up ANTHROPIC_API_KEY / LHX_SDK_* knobs.
    from .env import load_dotenv

    load_dotenv()

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
