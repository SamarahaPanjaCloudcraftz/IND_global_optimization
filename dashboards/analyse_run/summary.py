"""The one-look view: the top-ranked variant of every axis x weekday table.

Each cell is rank 1 of exactly the table the detail view would show for that
axis and weekday — same margin normalisation, same baseline, same ranking. The
only difference is where the filters live: here there is one set per axis,
applied to all of its weekdays.
"""

from collections.abc import Callable

import pandas as pd
import streamlit as st

import baseline
import filters
import margin
import ranking

MONEY = "{:,.0f}"
FORMATS = {"Final P&L": MONEY, "Max drawdown": MONEY, "Sortino": "{:.2f}"}
METRICS = list(FORMATS)
# Axis tables only: shown as values, not as a change from the baseline.
ROBUST_FORMATS = {"DSR": "{:.3f}", "CSCV": "{:.3f}"}


def _metrics(keys: list[str], choices: dict, config_of: dict, curve: Callable,
             reference: float, selling: int | None = None) -> pd.DataFrame:
    frames = {k: curve(choices[k], selling) for k in keys}
    frames = {k: f for k, f in frames.items() if not f.empty}
    margins = {k: margin.margin_factor(config_of[choices[k].digest]) for k in frames}
    return ranking.table(frames, margins, reference, robust=True) if frames else pd.DataFrame()


def _show(rows: list[dict], numeric: bool = True) -> None:
    table = pd.DataFrame(rows)
    shown = table.style.format(FORMATS, na_rep="—") if numeric else table.fillna("")
    st.dataframe(shown, hide_index=True, width="stretch")


def _against(value: float, reference: float, form: str) -> str:
    """`value (±x.x%)`, the change from the baseline. Every metric here is
    higher-is-better, so positive always means an improvement."""
    change = "—" if not reference else f"{(value - reference) / abs(reference) * 100:+.1f}%"
    return f"{form.format(value)} ({change})"


def render(scope: str, roots: list[str], weekdays_of: dict[str, list[str]],
           variants: Callable, config_of: dict, curve: Callable,
           selling_day_axes: set[str], bases: list[str], basis_note: str,
           choose_n: bool = False) -> dict:
    """Draw the summary and return the winners it picked.

    `variants(root, weekday)` -> (choices, branch_of, base job, baseline keys).
    `curve(job, weekday)` gives a job's equity, restricted to one weekday's P&L
    when a weekday is given — used by axes in `selling_day_axes` when toggled.

    With `choose_n`, each axis box has its own n — how many of its best
    variants to pick — otherwise every axis picks one.

    Returns {axis: {weekday: [(job, is_baseline, display label), ...]}}, the top
    n best first, leaving out a weekday where nothing was picked.
    """
    winners: dict[str, dict] = {}
    st.caption(("The top n variants of each axis × weekday table, n set per axis, "
                if choose_n else "The best variant of each axis × weekday table, ") +
               "picked by the multi-objective selector unless a box's \"Top by\" picks the "
               "composite or a single metric, on the same table as the detail view. "
               "P&L and drawdown are in ₹ "
               "on the baseline's margin. Brackets show the change from that weekday's "
               "baseline — positive is better, for drawdown too. Filters under an axis "
               "apply to all of its weekdays.")

    weekdays = [w for w in baseline.WEEKDAYS
                if any(w in days for days in weekdays_of.values())]
    rows = []
    for weekday in weekdays:
        choices, _, base, is_base = variants(roots[0], weekday)
        if not base:
            continue
        key = next(iter(is_base))
        ref = margin.margin_factor(config_of[base.digest])
        row = _metrics([key], choices, config_of, curve, ref)
        rows.append({"Weekday": weekday, **(row.iloc[0][METRICS].to_dict() if len(row) else {})})
    if rows:
        with st.container(border=True):
            st.subheader("Baseline", icon=":material/flag:")
            _show(rows)

    for root in roots:
        per_day, candidates, mode_of, weekday_of = {}, {}, {}, {}
        for weekday in weekdays_of[root]:
            choices, branch_of, base, is_base = variants(root, weekday)
            per_day[weekday] = (choices, branch_of, base, is_base)
            for key, job in choices.items():
                if key in is_base or job.digest not in config_of:
                    continue
                cell = f"{weekday}|{key}"
                candidates[cell] = config_of[job.digest]
                mode_of[cell] = branch_of[key][0]
                weekday_of[cell] = str(baseline.WEEKDAYS.index(weekday))

        with st.container(border=True):
            st.subheader(root, icon=":material/emoji_events:")
            with st.container(horizontal=True, gap="large"):
                basis = st.segmented_control("Top by", ranking.TOP_BY, default=ranking.SELECTOR,
                                             key=f"{scope}:summary:{root}:by") or ranking.SELECTOR
                n = int(st.number_input(
                    "n", min_value=1, max_value=5, value=1, step=1,
                    key=f"{scope}:combine:n:{root}",
                    help="How many of this axis's best variants carry into the combinations. "
                         "Round 1 combines every mix across the axes, so backtests multiply.")
                        ) if choose_n else 1
                selling_only = root in selling_day_axes and st.segmented_control(
                    "P&L basis", bases, default=bases[0],
                    key=f"{scope}:summary:{root}:basis") == bases[1]
            if selling_only:
                st.caption(basis_note)
            passing = filters.render(f"{scope}:summary:{root}", candidates, mode_of, weekday_of)
            pooled = any(mode_of.values())
            rows, notes = [], []
            for weekday in weekdays_of[root]:
                choices, branch_of, base, is_base = per_day[weekday]
                kept = [k for k in choices if f"{weekday}|{k}" in passing]
                picked, reason = pd.DataFrame(), "no variant matches the filters"
                if kept:
                    ref = margin.margin_factor(config_of[base.digest]) if base else 1.0
                    day = baseline.WEEKDAYS.index(weekday) if selling_only else None
                    table = _metrics(kept + sorted(is_base), choices, config_of, curve, ref, day)
                    picked, said = ranking.top(table, basis, n)
                    notes += [f"{weekday}: {note}" for note in said]
                    reference = table[table["Variant"].isin(is_base)] if len(table) else table
                    reason = ("nothing picked — see the note below" if len(table)
                              else "no equity data in the date range")
                for position, (_, top) in enumerate(picked.iterrows(), start=1):
                    row = {"Weekday": weekday, **({"#": position} if n > 1 else {})}
                    mode, label = branch_of[top["Variant"]]
                    if pooled:
                        row["Mode / Method"] = mode or "—"
                    row["Top variant"] = "baseline" if top["Variant"] in is_base else label
                    shown = "baseline" if top["Variant"] in is_base else (
                        f"{mode} · {label}" if mode else label)
                    winners.setdefault(root, {}).setdefault(weekday, []).append(
                        (choices[top["Variant"]], top["Variant"] in is_base, shown))
                    for name in METRICS:
                        row[name] = (_against(top[name], reference.iloc[0][name], FORMATS[name])
                                     if len(reference) else FORMATS[name].format(top[name]))
                    for name, form in ROBUST_FORMATS.items():
                        row[name] = "—" if pd.isna(top.get(name)) else form.format(top[name])
                    pbo = table.attrs.get("PBO")
                    row["Table PBO"] = "—" if pbo is None or pd.isna(pbo) else f"{pbo:.2f}"
                    rows.append(row)
                if picked.empty:
                    row = {"Weekday": weekday, **({"#": "—"} if n > 1 else {})}
                    if pooled:
                        row["Mode / Method"] = "—"
                    row["Top variant"] = reason
                    rows.append(row)
            _show(rows, numeric=False)
            for note in notes:
                st.caption(note)
    return winners
