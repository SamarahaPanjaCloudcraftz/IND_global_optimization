"""Run a plan's jobs to completion, independent of any dashboard session.

    python run_plan.py <run>/<stage>/plan.pickle

The dashboard starts this detached. A sweep of dozens of backtests must not
depend on a browser tab staying open: Streamlit reruns its script on every
widget interaction, so an in-process loop dies at its next `st.*` call. Here,
restarting the dashboard, clicking a widget or closing the tab change nothing.

Progress goes to `status.json` beside the plan, which is what the dashboard
reads - so it shows the live state of a sweep it did not start.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plan_io as P


def main(pickle_path: str, resume: bool = False) -> int:
    plan = P.read_plan(pickle_path)
    run_one = P.load_run_one(plan.runner)
    failed = 0
    for index, job, ok, detail in P.launch(plan, run_one, resume=resume):
        failed += not ok
        print(f"{index}/{len(plan.jobs)} {'ok' if ok else 'FAILED'} "
              f"{job.axis or 'control'} {detail}", flush=True)
    print(f"finished {len(plan.jobs)} jobs, {failed} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    arguments = sys.argv[1:]
    resuming = "--resume" in arguments
    arguments = [a for a in arguments if a != "--resume"]
    if len(arguments) != 1:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} <plan.pickle> [--resume]")
    raise SystemExit(main(arguments[0], resuming))
