"""Ranking variants within an axis.

Each metric is computed on margin-normalised P&L, ranked (1 = best, ties share
the average rank), and the ranks are combined by weighted sum into a composite
where lower is better. Metrics, directions and weights all live here.

A table can also be ordered by the multi-objective selector (`selector/`),
driven by `selector/default.toml`, whose metric names are this table's columns.
"""

from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

import selector
import series

WEIGHTS = {"Final P&L": 1.0, "Max drawdown": 1.0, "Sortino": 1.0}

# True: a larger value ranks better.
HIGHER_IS_BETTER = {"Final P&L": True, "Max drawdown": True, "Sortino": True}


SELECTOR = "Selector"
TOP_BY = [SELECTOR, "Composite", "Final P&L", "Sortino", "Max drawdown"]
SELECTOR_CONFIG = Path(__file__).resolve().parent / "selector" / "default.toml"


@lru_cache(maxsize=1)
def _selector_config() -> "selector.Config":
    return selector.load(SELECTOR_CONFIG)


def top(table: pd.DataFrame, basis: str, n: int = 1) -> tuple[pd.DataFrame, list[str]]:
    """The best `n` rows of a ranked table on one basis, best first, and any
    notes to show with them.

    Composite and single-metric bases order by rank (1 = best; a metric's ties
    fall to the composite). The selector picks with its own Pareto-and-score
    method; when it can pick nothing — every variant invalid, or a config or
    data error — no rows come back and the notes say why. It never falls back
    to another basis.
    """
    if table.empty:
        return table, []
    if basis == SELECTOR:
        try:
            result = selector.select(table, replace(_selector_config(), n=n))
        except (selector.ConfigError, selector.DataError) as error:
            return table.iloc[0:0], [f"Selector: {error}"]
        notes = [f"Selector: {warning}" for warning in result.run["warnings"]]
        rows = table.set_index("Variant", drop=False).loc[result.selected]
        return rows.reset_index(drop=True), notes
    if basis == "Composite":
        ordered = table
    else:
        ordered = table.sort_values([f"{basis} rank", "Composite"], kind="stable")
    return ordered.head(n).reset_index(drop=True), []


def best(table: pd.DataFrame, basis: str) -> pd.Series | None:
    """The best row on one basis, or None when the basis picks nothing."""
    rows, _ = top(table, basis, 1)
    return rows.iloc[0] if len(rows) else None


def selector_audit(table: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """The table with the selector's verdict on every row: its Pareto front,
    score (lower is better), rank, status and reason. Notes as for `top`."""
    if table.empty:
        return table, []
    try:
        result = selector.select(table, replace(_selector_config(), n=1))
    except (selector.ConfigError, selector.DataError) as error:
        return table, [f"Selector: {error}"]
    audit = result.audit.rename(columns={"front": "Front", "score": "Selector score",
                                         "rank": "Selector rank", "status": "Selector status",
                                         "reason": "Selector reason"})
    keep = ["Variant", "Front", "Selector score", "Selector rank", "Selector status",
            "Selector reason"]
    merged = table.merge(audit[keep], on="Variant", how="left", validate="one_to_one")
    return merged, [f"Selector: {warning}" for warning in result.run["warnings"]]


def sortino(daily: pd.Series) -> float:
    """Mean over downside deviation, on days with non-zero P&L only, target 0."""
    traded = daily[daily != 0]
    if traded.empty:
        return np.nan
    downside = np.sqrt((traded.clip(upper=0) ** 2).mean())
    return np.inf if downside == 0 else traded.mean() / downside


def metrics(equity: pd.DataFrame, margin: float) -> dict:
    if equity.empty or not margin:
        return {name: np.nan for name in WEIGHTS}
    scaled = equity.assign(equity=equity["equity"] / margin)
    return {
        "Final P&L": scaled["equity"].iloc[-1],
        "Max drawdown": series.drawdown(scaled)["drawdown"].min(),
        "Sortino": sortino(series.pnl(scaled, "Daily")["pnl"]),
    }


def table(frames: dict[str, pd.DataFrame], margins: dict[str, float],
          reference: float = 1.0) -> pd.DataFrame:
    """Ranked metrics, with money expressed at `reference` margin.

    Scaling every variant by the same reference changes no rank; it only puts
    the per-margin numbers back into rupees at a familiar size.
    """
    rows = [{"Variant": label, **metrics(frame, margins.get(label, 0.0))}
            for label, frame in frames.items()]
    out = pd.DataFrame(rows)
    out["Final P&L"] *= reference
    out["Max drawdown"] *= reference
    composite = 0.0
    for name, weight in WEIGHTS.items():
        rank = out[name].rank(method="average", ascending=not HIGHER_IS_BETTER[name],
                              na_option="bottom")
        out[f"{name} rank"] = rank
        composite = composite + weight * rank
    out["Composite"] = composite
    return out.sort_values("Composite").reset_index(drop=True)
