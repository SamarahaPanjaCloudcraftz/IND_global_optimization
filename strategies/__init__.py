"""Per-strategy parameter trees.

One module per strategy, each defining a subclass of a shape class and exposing
one instance as `STRATEGY`. The instance carries:

    name      how it appears in the dashboard
    runner    import path "module:attribute" of the function that runs one
              backtest, called as run_one(job, out_dir). The runner belongs to
              the strategy because it is the backtest engine the strategy runs
              on, not a per-run choice.
    tree      the Node defining the universe: which parameters may be
              optimized, and which of them interact
    baseline  a complete config, one value per parameter

plus the declarations the planning dashboard reads — groups, shared, varies,
baselines, gated, pending.

The tree is the strategy author's hypothesis about what interacts (product) and
what is independent (sum). It is not a user setting.
"""

import importlib
import pkgutil
from pathlib import Path


def discover() -> dict:
    """Every strategy in this package, keyed by its name."""
    found = {}
    for info in pkgutil.iter_modules([str(Path(__file__).parent)]):
        module = importlib.import_module(f"{__name__}.{info.name}")
        strategy = getattr(module, "STRATEGY", None)
        if strategy is not None:
            found[strategy.name] = strategy
    return found
