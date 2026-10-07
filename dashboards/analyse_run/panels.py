"""The panels a page can show, and the registry that finds them.

Adding a view means adding one function with `@panel("Name")`. The page renders
whatever is selected and never learns what any panel does, so views can come and
go without the page changing — which is the point, since what we want to look at
will keep moving.

A panel takes a View and draws. It may render its own controls; anything it
needs beyond the selected variants is its own business.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd
import streamlit as st

import charts
import ranking
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
    margins: dict[str, float] = field(default_factory=dict)
    reference: float = 1.0      # margin that money in the ranking is expressed at
    branches: dict[str, tuple[str, str]] = field(default_factory=dict)  # key -> (mode, label)
    baseline: str | None = None  # key of this weekday's baseline, when the stage has one

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


@panel("Ranking")
def _ranking(view: View) -> None:
    st.caption("P&L and drawdown are in ₹, as each variant would earn on the baseline's "
               "margin. Ranks: 1 is best, ties share the average. Composite is the "
               "weighted sum of the three ranks — lower is better. The Selector columns "
               "are the multi-objective selector's verdict: Pareto front (1 = beaten by "
               "nothing), score (lower is better), its rank, status and reason.")
    table, notes = ranking.selector_audit(ranking.table(view.frames, view.margins, view.reference))
    for note in notes:
        st.caption(note)
    keys = table["Variant"].copy()
    if any(middle for middle, _ in view.branches.values()):
        table.insert(0, "Mode / Method", [view.branches.get(k, ("", k))[0] or "—" for k in keys])
        table["Variant"] = [view.branches.get(k, ("", k))[1] for k in keys]
    rupees = "{:,.0f}"
    formats = {"Final P&L": rupees, "Max drawdown": rupees, "Sortino": "{:.2f}"}
    formats.update({c: "{:g}" for c in table.columns if c.endswith("rank") or c == "Composite"})
    formats["Selector score"] = "{:.3f}"

    # The baseline is ranked with everything else; its row is repeated above the
    # table so the reference numbers are in view however far down it ranks.
    if view.baseline in set(keys):
        row = table[keys == view.baseline]
        st.markdown(f"**Baseline** — ranked {int(row.index[0]) + 1} of {len(table)}")
        st.dataframe(row.style.format(formats, na_rep="—"), hide_index=True, width="stretch")
    st.dataframe(table.style.format(formats, na_rep="—"), hide_index=True, width="stretch")


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
    for label, frame in view.frames.items():
        st.markdown(f"**{label}**")
        st.altair_chart(charts.pnl_bars(series.pnl(frame, grain), f"{grain} P&L",
                                        view.dark, height=180))


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
    st.altair_chart(charts.pnl_bars(frame, f"{grain} difference", view.dark))
    total = frame["pnl"].sum()
    st.metric("Total difference over the period", f"{total:,.0f}", border=True)
