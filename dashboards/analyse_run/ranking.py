"""Ranking variants within an axis.

Each metric is computed on margin-normalised P&L, ranked (1 = best, ties share
the average rank), and the ranks are combined by weighted sum into a composite
where lower is better. Metrics, directions and weights all live here.

A table can also be ordered by the multi-objective selector (`selector/`),
whose metric names are this table's columns.

Axis tables (one axis x one weekday) add two overfitting-aware metrics, the
Deflated Sharpe Ratio and a CSCV consistency score, and carry the table's PBO
(see robustness.py and docs/DSR_and_CSCV.md); they rank with
`selector/axis.toml`. The Combine page's best-of and recommendation tables do
not, and rank with `selector/default.toml`.
"""

from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

import robustness
import selector
import series

WEIGHTS = {"Final P&L": 1.0, "Max drawdown": 1.0, "Sortino": 1.0}
# Axis tables: Sortino and DSR both measure return per unit of risk, so they
# share one unit of weight, as they share one group in selector/axis.toml.
ROBUST_WEIGHTS = {"Final P&L": 1.0, "Max drawdown": 1.0, "Sortino": 0.5, "DSR": 0.5, "CSCV": 1.0}
ROBUST = ("DSR", "CSCV")

# True: a larger value ranks better.
HIGHER_IS_BETTER = {"Final P&L": True, "Max drawdown": True, "Sortino": True,
                    "DSR": True, "CSCV": True}


SELECTOR = "Selector"
TOP_BY = [SELECTOR, "Composite", "Final P&L", "Sortino", "Max drawdown"]
SELECTOR_CONFIG = Path(__file__).resolve().parent / "selector" / "default.toml"
AXIS_SELECTOR_CONFIG = Path(__file__).resolve().parent / "selector" / "axis.toml"


@lru_cache(maxsize=2)
def _load(path: Path) -> "selector.Config":
    return selector.load(path)


def _selector_config(table: pd.DataFrame) -> "selector.Config":
    """axis.toml for a table carrying DSR and CSCV, default.toml otherwise."""
    return _load(AXIS_SELECTOR_CONFIG if set(ROBUST) <= set(table.columns) else SELECTOR_CONFIG)


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
            result = selector.select(table, replace(_selector_config(table), n=n))
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
        result = selector.select(table, replace(_selector_config(table), n=1))
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
          reference: float = 1.0, robust: bool = False) -> pd.DataFrame:
    """Ranked metrics, with money expressed at `reference` margin.

    Scaling every variant by the same reference changes no rank; it only puts
    the per-margin numbers back into rupees at a familiar size.

    With `robust` (axis tables) the table also gets DSR and CSCV, computed
    across exactly these variants, and its PBO in `table.attrs["PBO"]`.
    """
    rows = [{"Variant": label, **metrics(frame, margins.get(label, 0.0))}
            for label, frame in frames.items()]
    out = pd.DataFrame(rows)
    out["Final P&L"] *= reference
    out["Max drawdown"] *= reference
    weights = WEIGHTS
    pbo = np.nan
    if robust:
        # Ratios: margin scaling cancels, so the raw daily P&L is used. Only
        # variants with valid basic metrics take part, and N counts them.
        valid = [label for label, value in zip(out["Variant"], out["Final P&L"]) if np.isfinite(value)]
        daily = {label: series.pnl(frames[label], "Daily").set_index("date")["pnl"] for label in valid}
        dsr, _ = robustness.dsr_table(daily)
        consistency, pbo = robustness.cscv_table(daily)
        out["DSR"] = out["Variant"].map(dsr).astype(float)
        out["CSCV"] = out["Variant"].map(consistency).astype(float)
        weights = ROBUST_WEIGHTS
    composite = 0.0
    for name, weight in weights.items():
        rank = out[name].rank(method="average", ascending=not HIGHER_IS_BETTER[name],
                              na_option="bottom")
        out[f"{name} rank"] = rank
        composite = composite + weight * rank
    out["Composite"] = composite
    out = out.sort_values("Composite").reset_index(drop=True)
    if robust:
        out.attrs["PBO"] = pbo
    return out
