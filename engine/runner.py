"""The interface between the tree and the backtest engine.

Its sole job is to receive jobs and run all of them. It knows nothing about what
a backtest is: `run_one` is supplied by the caller and owns everything
engine-specific.

Where a job's output goes is the plan's business, not the runner's, so the
caller passes a `directory` function rather than letting run_one guess.
"""

from collections.abc import Callable, Iterator


def run_all(jobs, run_one: Callable, directory: Callable | None = None) -> Iterator[tuple]:
    """Run every job, yielding (job, ok, detail) as each one finishes.

    `run_one` is called as `run_one(job, directory(job))`, or as `run_one(job)`
    when no directory function is given.

    A job that raises is reported rather than propagated: one bad config should
    not cost you the rest of an overnight sweep.
    """
    for job in jobs:
        try:
            detail = run_one(job, directory(job)) if directory else run_one(job)
            yield job, True, detail
        except Exception as error:  # noqa: BLE001 - reported to the caller
            yield job, False, f"{type(error).__name__}: {error}"
