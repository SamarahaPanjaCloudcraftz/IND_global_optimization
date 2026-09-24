"""Job 1 - the run plan: generate -> overlay -> dedup -> backtest jobs.

The two passes here live outside the nodes so the nodes stay pure structure.
"""

from dataclasses import dataclass

from .tree import Internal, Node, Sum, branches


@dataclass(frozen=True)
class PlanSize:
    """How big a run plan is, for estimating how long it will take."""

    variants: int  # what the tree emits, before dedup
    runs: int      # backtests that will actually run
    deduped: int   # configs that collapsed into the single control


@dataclass(frozen=True)
class Job:
    """One complete config to backtest, tagged with the axis it came from."""

    config: dict
    # The branch this came from, e.g. "delta_hedging/gamma_iv/Monday".
    # None = the standalone control run.
    axis: str | None


def axes(root: Node, only: list[str] | None = None) -> list[Node]:
    """The independent axes: the children of the root sum, or the root itself.

    `only` picks a subset by name, for optimizing one set of axes before another.
    """
    if isinstance(root, Internal) and isinstance(root.combiner, Sum):
        found = list(root.children)
    else:
        found = [root]
    if only is None:
        return found
    by_name = {axis.name: axis for axis in found}
    unknown = [name for name in only if name not in by_name]
    if unknown:
        raise KeyError(f"no such axis: {unknown}; known axes are {sorted(by_name)}")
    return [by_name[name] for name in only]


def freeze(value):
    """A canonical, hashable form of a config value.

    Swept values include dicts (per-weekday constants) and lists, which are not
    hashable. Dict keys are sorted, so writing the same mapping in a different
    order gives the same form. The sequence type is kept, because a list and an
    equal tuple are written out as different source and so are different configs.
    """
    if isinstance(value, dict):
        return ("dict", tuple(sorted((freeze(k), freeze(v)) for k, v in value.items())))
    if isinstance(value, (list, tuple)):
        return (type(value).__name__, tuple(freeze(v) for v in value))
    return value


def canonical(config: dict):
    """A config reduced to one comparable, hashable value."""
    return tuple(sorted((name, freeze(value)) for name, value in config.items()))


def _key(config: dict):
    return canonical(config)


def build_jobs(root: Node, baseline: dict, only: list[str] | None = None) -> list[Job]:
    """The full set of backtest jobs, each a complete config.

    Overlay completes every partial on the baseline; dedup collapses the
    all-baseline config that each axis emits into the single standalone control.

    `only` restricts the run to a subset of axes. To stage a one-way dependency
    x -> y, build and run x's axes, compose their winners, and pass that config
    in as the baseline for y's axes.
    """
    jobs = [Job(config=dict(baseline), axis=None)]
    seen = {_key(baseline)}
    for axis in axes(root, only):
        for sub, group in branches(axis):
            tag = f"{axis.name}/{sub}" if sub else axis.name
            for partial in group.generate():
                config = {**baseline, **partial}
                key = _key(config)
                if key in seen:
                    continue
                seen.add(key)
                jobs.append(Job(config=config, axis=tag))
    return jobs


def plan_size(root: Node, baseline: dict, only: list[str] | None = None) -> PlanSize:
    """The post-dedup number of backtests, and how many duplicates collapsed."""
    variants = sum(axis.count() for axis in axes(root, only))
    runs = len(build_jobs(root, baseline, only))
    return PlanSize(variants=variants, runs=runs, deduped=variants + 1 - runs)
