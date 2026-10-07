"""Standalone multi-objective variant selector. See DESIGN.md.

    from selector import select
    result = select(table, "selector/default.toml")
    result.selected   # top-n variant IDs, best first
    result.audit      # one row per input variant: status, reason, front, gaps, score, rank
    result.run        # resolved signs, eps, weights, ideal/nadir, warnings, diagnostic
"""

from .pipeline import Selection, select
from .spec import Config, ConfigError, Constraint, Metric, load
from .stages import DataError

__all__ = ["select", "Selection", "load", "Config", "Metric", "Constraint",
           "ConfigError", "DataError"]
