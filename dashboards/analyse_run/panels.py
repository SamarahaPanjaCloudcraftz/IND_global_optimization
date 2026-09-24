"""The panels a page can show, and the registry that finds them.

Adding a view means adding one function with `@panel("Name")`. The page renders
whatever is selected and never learns what any panel does, so views can come and
go without the page changing — which is the point, since what we want to look at
will keep moving.

A panel takes a View and draws. It may render its own controls; anything it
needs beyond the selected variants is its own business.
"""

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd
import streamlit as st

import charts
import series

PANELS: dict[str, Callable] = {}


def panel(name: str):
    """Register a panel under a display name."""
    def register(function):
        PANELS[name] = function
        return function
    return register


@dataclass
class View:
    """What every panel is given: the selected variants and how to colour them."""

    axis: str
    frames: dict[str, pd.DataFrame]   # variant label -> (date, equity)
    colours: dict[str, str]
    dark: bool

    def key(self, suffix: str) -> str:
        return f"{self.axis}:{suffix}"


def _too_many(view: View) -> None:
    """Say so when a line chart cannot show every selected variant."""
    hidden = len(view.frames) - len(view.colours)
    if hidden > 0:
        st.caption(
            f"Showing {len(view.colours)} of {len(view.frames)} variants — beyond "
            f"{charts.MAX_LINES} the hues would repeat and read as the wrong series. "
            "Narrow the selection to compare the rest."
        )


def _grain(view: View, suffix: str) -> str:
    return st.segmented_control(
        "Granularity", list(series.GRAINS), default="Daily",
        key=view.key(suffix), label_visibility="collapsed",
    ) or "Daily"


@panel("Summary")
def _summary(view: View) -> None:
    rows = [{"Variant": label, **series.summary(frame)}
            for label, frame in view.frames.items()]
    table = pd.DataFrame(rows)
    money = [c for c in table.columns
             if c in ("Final P&L", "Max drawdown", "Best day", "Worst day")]
    st.dataframe(
        table, hide_index=True,
        column_config={c: st.column_config.NumberColumn(c, format="%,.0f") for c in money},
    )


@panel("Equity")
def _equity(view: View) -> None:
    _too_many(view)
    st.altair_chart(charts.equity(view.frames, view.colours))


@panel("Drawdown")
def _drawdown(view: View) -> None:
    st.caption("Distance below each variant's own running peak, in currency.")
    _too_many(view)
    st.altair_chart(charts.drawdown(
        {label: series.drawdown(frame) for label, frame in view.frames.items()},
        view.colours,
    ))


@panel("P&L bars")
def _pnl_bars(view: View) -> None:
    grain = _grain(view, "pnl-grain")
    tidy = pd.concat(
        [series.pnl(frame, grain).assign(variant=label)
         for label, frame in view.frames.items()],
        ignore_index=True,
    )
    st.altair_chart(charts.pnl_bars(tidy, f"{grain} P&L", view.dark))


@panel("Difference")
def _difference(view: View) -> None:
    labels = list(view.frames)
    if len(labels) < 2:
        st.info("Select at least two variants to compare.", icon=":material/info:")
        return
    with st.container(horizontal=True, vertical_alignment="bottom"):
        left = st.selectbox("A", labels, index=0, key=view.key("diff-a"))
        right = st.selectbox("B", labels, index=1, key=view.key("diff-b"))
    grain = _grain(view, "diff-grain")
    if left == right:
        st.info("Pick two different variants.", icon=":material/info:")
        return
    frame = series.difference(view.frames[left], view.frames[right], grain)
    st.caption(f"**A − B** · positive means *{left}* earned more in that period.")
    st.altair_chart(charts.pnl_bars(frame, f"{grain} difference", view.dark, facet=False))
    total = frame["pnl"].sum()
    st.metric("Total difference over the period", f"{total:,.0f}", border=True)
