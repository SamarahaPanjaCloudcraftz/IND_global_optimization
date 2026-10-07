"""Data-side stages: validation, canonical form and hard constraints
(DESIGN.md §6, §7, §4).

Three views of one metric value x:
  natural   - what constraints are written against: |x| for a metric with a
              sign convention, x otherwise.
  canonical - "higher is better": x for max, -x for min, -|x| for min with a
              sign convention. Everything after this stage uses only this.
"""

import operator

import numpy as np
import pandas as pd

from .spec import Config, Metric

COMPARE = {"<=": operator.le, "<": operator.lt, ">=": operator.ge, ">": operator.gt}
LISTED = 10  # offending variants named in an error message before "and N more"


class DataError(ValueError):
    """The input table can't be used; the run stops."""


def _names(ids, limit: int = LISTED) -> str:
    ids = list(ids)
    shown = ", ".join(map(str, ids[:limit]))
    return shown if len(ids) <= limit else f"{shown} and {len(ids) - limit} more"


def validate(table, config: Config):
    """Check the table (D1-D11).

    Returns (ids, values, invalid, signs):
      ids     - original IDs, ordered by their string form
      values  - DataFrame of float metric values, indexed by the string IDs in that order
      invalid - string ID -> rejection reason, for variants with NaN or inf
      signs   - metric name -> resolved sign ("auto" resolved; None if nothing valid to look at)
    """
    if not isinstance(table, pd.DataFrame):
        raise DataError(f"D1 table must be a pandas DataFrame, got {type(table).__name__}")
    if table.empty:
        raise DataError("D1 table has no rows")
    id_column = config.id_column
    if id_column not in table.columns:
        raise DataError(f"D2 ID column {id_column!r} not in table (columns: {list(table.columns)})")
    raw_ids = table[id_column]
    if raw_ids.isna().any():
        raise DataError(f"D3 {int(raw_ids.isna().sum())} variant(s) have a null ID")
    keys = raw_ids.astype(str)
    duplicated = keys[keys.duplicated(keep=False)]
    if not duplicated.empty:
        raise DataError(f"D4 duplicate IDs: {_names(sorted(set(duplicated)))}")
    missing = [m.name for m in config.metrics if m.name not in table.columns]
    if missing:
        raise DataError(f"D5 metric column(s) missing from table: {missing}")
    for m in config.metrics:
        column = table[m.name]
        if pd.api.types.is_bool_dtype(column) or not pd.api.types.is_numeric_dtype(column):
            raise DataError(f"D6 metric column {m.name!r} is not numeric (dtype {column.dtype})")

    order = np.argsort(keys.to_numpy(dtype=str), kind="stable")
    ids = list(raw_ids.to_numpy()[order])
    index = pd.Index(keys.to_numpy(dtype=str)[order], name=id_column)
    values = pd.DataFrame({m.name: table[m.name].to_numpy(dtype=float, na_value=np.nan)[order]
                           for m in config.metrics}, index=index)

    invalid: dict[str, str] = {}
    for key, row in values.iterrows():
        reasons = [f"NaN in {name}" for name, v in row.items() if np.isnan(v)]
        reasons += [f"inf in {name}" for name, v in row.items() if np.isinf(v)]
        if reasons:
            invalid[key] = "; ".join(reasons)

    valid = values.loc[~values.index.isin(list(invalid))]
    signs = {m.name: _resolve_sign(m, valid[m.name]) for m in config.metrics}
    return ids, values, invalid, signs


def _resolve_sign(metric: Metric, column: pd.Series) -> str | None:
    """Check a declared sign against the valid values (D9, D10)."""
    if metric.sign == "none":
        return "none"
    if column.empty:
        return None if metric.sign == "auto" else metric.sign
    positive, negative = column[column > 0], column[column < 0]
    if metric.sign == "negative" and not positive.empty:
        raise DataError(f"D9 {metric.name!r} is declared sign = \"negative\" but is > 0 for: "
                        f"{_names(positive.index)}")
    if metric.sign == "positive" and not negative.empty:
        raise DataError(f"D9 {metric.name!r} is declared sign = \"positive\" but is < 0 for: "
                        f"{_names(negative.index)}")
    if metric.sign == "auto":
        if not positive.empty and not negative.empty:
            raise DataError(f"D9 {metric.name!r} (sign = \"auto\") has mixed signs: "
                            f"> 0 for {_names(positive.index)}; < 0 for {_names(negative.index)}")
        return "negative" if not negative.empty else "positive"
    return metric.sign


def natural(metric: Metric, x):
    return np.abs(x) if metric.magnitude else x


def canonical(metric: Metric, x):
    if metric.direction == "max":
        return x
    return -np.abs(x) if metric.magnitude else -x


def constraints(values: pd.DataFrame, config: Config) -> tuple[dict[str, list[str]], list[dict]]:
    """Apply the hard constraints to the valid variants.

    Returns (failed, diagnostic): failed maps string ID -> the constraints it
    broke, each with the variant's natural value; diagnostic has one entry per
    constraint with how many variants it removes on its own and the best natural
    value any variant reached.
    """
    failed: dict[str, list[str]] = {}
    diagnostic = []
    for c in config.constraints:
        nat = natural(config.metric(c.metric), values[c.metric])
        passes = COMPARE[c.op](nat, c.value)
        for key, value in nat[~passes].items():
            failed.setdefault(key, []).append(f"{c} (was {value:g})")
        best = nat.min() if c.op in ("<=", "<") else nat.max()
        diagnostic.append({"constraint": str(c), "removes": int((~passes).sum()),
                           "best_value": None if nat.empty else float(best)})
    return failed, diagnostic
