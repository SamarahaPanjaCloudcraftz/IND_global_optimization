"""Turning an equity curve into the things worth looking at.

Pure pandas: every function takes frames and returns frames. No Streamlit, no
charts, no file paths — so each one is readable and testable on its own, and a
new view usually means a new function here rather than a change to an old one.

Equity is cumulative P&L in currency starting from zero, so period P&L is its
first difference and drawdown is a currency distance from the running peak. A
percentage drawdown is deliberately absent: it would divide by a base that
starts at zero.
"""

import pandas as pd

GRAINS = {"Daily": "D", "Weekly": "W-FRI", "Monthly": "ME"}


def window(equity: pd.DataFrame, start, end) -> pd.DataFrame:
    """The curve between start and end inclusive, re-based to zero at start.

    Re-basing subtracts the equity already earned before the window, so every
    metric of the result describes only what happened inside it.
    """
    if equity.empty:
        return equity
    dates = equity["date"]
    earlier = equity.loc[dates < pd.Timestamp(start), "equity"]
    offset = earlier.iloc[-1] if not earlier.empty else 0.0
    inside = equity[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
    return inside.assign(equity=inside["equity"] - offset).reset_index(drop=True)


def selling_day(equity: pd.DataFrame, weekday: int) -> pd.DataFrame:
    """Only the P&L made on one weekday, re-cumulated into an equity curve.

    Each day's P&L is the curve's first difference, so this keeps the days that
    fall on `weekday` and sums them — what the book earned on its selling days
    alone, leaving out every day it was merely held.
    """
    if equity.empty:
        return equity
    steps = equity["equity"].diff()
    steps.iloc[0] = equity["equity"].iloc[0]
    keep = equity["date"].dt.weekday == weekday
    return pd.DataFrame({"date": equity["date"][keep],
                         "equity": steps[keep].cumsum()}).reset_index(drop=True)


def pnl(equity: pd.DataFrame, grain: str = "Daily") -> pd.DataFrame:
    """P&L per period, as (date, pnl).

    The first period counts from zero rather than being dropped, so the periods
    sum back to the final P&L exactly.
    """
    if equity.empty:
        return pd.DataFrame(columns=["date", "pnl"])
    series = equity.set_index("date")["equity"]
    if grain != "Daily":
        series = series.resample(GRAINS[grain]).last().dropna()
    steps = series.diff()
    steps.iloc[0] = series.iloc[0]
    return steps.rename("pnl").reset_index()


def drawdown(equity: pd.DataFrame) -> pd.DataFrame:
    """Distance below the running peak, as (date, drawdown). Zero or negative."""
    if equity.empty:
        return pd.DataFrame(columns=["date", "drawdown"])
    out = equity.copy()
    out["drawdown"] = out["equity"] - out["equity"].cummax()
    return out[["date", "drawdown"]]


def difference(left: pd.DataFrame, right: pd.DataFrame, grain: str = "Daily") -> pd.DataFrame:
    """left minus right, per period, as (date, pnl).

    Aligned on date with missing periods treated as no P&L, so two runs that
    traded on different days still compare.
    """
    a = pnl(left, grain).set_index("date")["pnl"]
    b = pnl(right, grain).set_index("date")["pnl"]
    both = a.subtract(b, fill_value=0.0)
    return both.rename("pnl").reset_index()
