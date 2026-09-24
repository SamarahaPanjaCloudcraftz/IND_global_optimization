"""Chart builders.

One function per chart, each taking tidy frames and returning an Altair chart —
so a new chart is a new function here, and no existing one has to change.

Colour follows the validated reference palette. Two rules from it are load
bearing rather than cosmetic:

  * Identity is categorical: a variant keeps its hue however the selection
    changes, so colour never tracks rank or sort order.
  * Sign is diverging: blue and red poles about a neutral zero. Red is loss,
    which is also the financial convention.

Both modes are selected steps of the same hues, not an automatic flip.
"""

import altair as alt
import pandas as pd

# Categorical slots, in the fixed order the palette validates. Lines use the
# adjacent pairlist, which all eight slots clear.
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500",
               "#d55181", "#008300", "#9085e9", "#e66767"]

GAIN_LIGHT, LOSS_LIGHT = "#2a78d6", "#e34948"
GAIN_DARK, LOSS_DARK = "#3987e5", "#e66767"

MONEY = ",.0f"
HEIGHT = 260
# Hues are never cycled: a ninth line would repeat a slot and read as a series
# it is not. Past this many, the line charts show the first few and say so.
MAX_LINES = len(SERIES_LIGHT)


def palette(dark: bool) -> list[str]:
    return SERIES_DARK if dark else SERIES_LIGHT


def colours(labels: list[str], dark: bool) -> dict[str, str]:
    """A hue per variant, assigned once and held, for the first MAX_LINES only.

    Bound to the label, so filtering the selection never repaints the survivors.
    """
    slots = palette(dark)
    return {label: slots[index] for index, label in enumerate(labels[:MAX_LINES])}


def _scale(assigned: dict[str, str]) -> alt.Scale:
    return alt.Scale(domain=list(assigned), range=list(assigned.values()))


def _lines(frames: dict[str, pd.DataFrame], value: str, title: str,
           assigned: dict[str, str]) -> alt.Chart:
    drawn = {label: frames[label] for label in assigned if label in frames}
    tidy = pd.concat(
        [frame.assign(variant=label) for label, frame in drawn.items() if not frame.empty],
        ignore_index=True,
    )
    hover = alt.selection_point(fields=["date"], nearest=True, on="mouseover",
                                empty=False, clear="mouseout")
    base = alt.Chart(tidy).encode(
        x=alt.X("date:T", title=None),
        y=alt.Y(f"{value}:Q", title=title, axis=alt.Axis(format="~s")),
        color=alt.Color("variant:N", title=None, scale=_scale(assigned),
                        legend=alt.Legend(orient="bottom", columns=1)
                        if len(drawn) > 1 else None),
    )
    line = base.mark_line(strokeWidth=2, interpolate="monotone")
    points = base.mark_point(size=60, filled=True, opacity=0).add_params(hover)
    marks = base.mark_point(size=60, filled=True).transform_filter(hover).encode(
        tooltip=[alt.Tooltip("date:T", title="Date"),
                 alt.Tooltip("variant:N", title="Variant"),
                 alt.Tooltip(f"{value}:Q", title=title, format=MONEY)],
    )
    rule = alt.Chart(tidy).mark_rule(strokeWidth=1, opacity=0.35).encode(
        x="date:T").transform_filter(hover)
    return (line + points + rule + marks).properties(height=HEIGHT)


def equity(frames: dict[str, pd.DataFrame], assigned: dict[str, str]) -> alt.Chart:
    """Cumulative P&L over time, one line per variant."""
    return _lines(frames, "equity", "Cumulative P&L", assigned)


def drawdown(frames: dict[str, pd.DataFrame], assigned: dict[str, str]) -> alt.Chart:
    """Distance below each variant's own running peak. Always at or below zero."""
    return _lines(frames, "drawdown", "Drawdown", assigned)


def pnl_bars(tidy: pd.DataFrame, title: str, dark: bool, facet: bool = True) -> alt.Chart:
    """P&L per period, coloured by sign. One row per variant when faceting."""
    gain, loss = (GAIN_DARK, LOSS_DARK) if dark else (GAIN_LIGHT, LOSS_LIGHT)
    chart = alt.Chart(tidy).mark_bar(cornerRadiusEnd=4).encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("pnl:Q", title=title, axis=alt.Axis(format="~s")),
        color=alt.condition(alt.datum.pnl >= 0, alt.value(gain), alt.value(loss)),
        tooltip=[alt.Tooltip("date:T", title="Period"),
                 alt.Tooltip("pnl:Q", title=title, format=MONEY)]
        + ([alt.Tooltip("variant:N", title="Variant")] if "variant" in tidy else []),
    ).properties(height=HEIGHT if not facet else 140)
    if facet and "variant" in tidy and tidy["variant"].nunique() > 1:
        return chart.facet(row=alt.Row("variant:N", title=None,
                                       header=alt.Header(labelAnchor="start")))
    return chart
