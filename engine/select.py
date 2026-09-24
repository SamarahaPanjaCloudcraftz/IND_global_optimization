"""Job 2 - choosing winners.

Selection fires once per child of the sum: each is one independent axis, so each
gets one winner picked from its own full variant-set. `select` never recurses
into a product - a product axis is an opaque bag of variants and its winner is a
whole cell, because its parameters interact.
"""

from dataclasses import dataclass

from .plan import Job


@dataclass(frozen=True)
class Result:
    """A job that has come back scored."""

    job: Job
    score: float


def argmax(candidates: list[Result]) -> Result:
    """Default `select`: the best backtest metric wins."""
    return max(candidates, key=lambda result: result.score)


def route(results: list[Result]) -> dict[str, list[Result]]:
    """Group scored runs by originating axis. The control joins every group."""
    control = [r for r in results if r.job.axis is None]
    groups: dict[str, list[Result]] = {}
    for result in results:
        if result.job.axis is not None:
            groups.setdefault(result.job.axis, []).append(result)
    return {axis: candidates + control for axis, candidates in groups.items()}


def choose(results: list[Result], select=argmax) -> dict[str, Result]:
    """One winner per independent axis."""
    return {axis: select(candidates) for axis, candidates in route(results).items()}


def compose(baseline: dict, winners: dict[str, Result]) -> dict:
    """Fold the per-axis winners into one config.

    Axes touch disjoint parameters, so each winner contributes only where it
    departs from the baseline.
    """
    config = dict(baseline)
    for result in winners.values():
        config.update(
            {k: v for k, v in result.job.config.items() if baseline[k] != v}
        )
    return config


def confirmation_job(config: dict) -> Job:
    """The composed config was (in general) never run. Run it once."""
    return Job(config=config, axis="confirmation")
