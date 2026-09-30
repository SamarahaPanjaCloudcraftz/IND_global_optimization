"""Ranking variants within an axis.

Each metric is computed on margin-normalised P&L, ranked (1 = best, ties share
the average rank), and the ranks are combined by weighted sum into a composite
where lower is better. Metrics, directions and weights all live here.
"""

import numpy as np
import pandas as pd

import series

WEIGHTS = {"Final P&L": 1.0, "Max drawdown": 1.0, "Sortino": 1.0}

# True: a larger value ranks better.
HIGHER_IS_BETTER = {"Final P&L": True, "Max drawdown": True, "Sortino": True}


TOP_BY = ["Composite", "Final P&L", "Sortino", "Max drawdown"]


def best(table: pd.DataFrame, basis: str) -> pd.Series:
    """The best row of a ranked table on one basis: the composite, or a single
    metric's rank. Ranks are already direction-aware (1 = best); ties fall to
    the composite."""
    if basis == "Composite":
        return table.iloc[0]
    return table.sort_values([f"{basis} rank", "Composite"], kind="stable").iloc[0]


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
