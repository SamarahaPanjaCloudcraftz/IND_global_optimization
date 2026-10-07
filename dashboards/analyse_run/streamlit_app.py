"""Analyse a run.

    streamlit run dashboards/analyse_run/streamlit_app.py

Takes a run directory and nothing else. Everything shown is read from what the
sweep wrote there — the plan, the per-job results, the equity curves — so this
page never needs to agree with the planning dashboard about anything.

The page itself is deliberately thin: pick a stage, an axis, some variants, some
panels, and hand each panel the selection. What the panels are lives in
`panels.py`; adding one does not touch this file.
"""

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baseline
import charts
import combination_page
import filters
import margin
import panels
import ranking
import runs
import series
import summary

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ROOT / "outputs"

st.set_page_config(page_title="Analyse run", page_icon=":material/insights:", layout="wide")


def latest_run() -> str:
    found = sorted((d for d in OUTPUTS.glob("*/") if d.is_dir()),
                   key=lambda d: d.stat().st_mtime, reverse=True)
    return str(found[0]).rstrip("/") if found else str(OUTPUTS)


st.session_state.setdefault("run_dir", latest_run())

with st.sidebar:
    st.subheader("Run", icon=":material/folder_open:")
    run_dir = st.text_input("Run directory", key="run_dir")
    directory = Path(run_dir)
    if not directory.is_dir():
        st.error("Not a directory.", icon=":material/error:")
        st.stop()
    available = runs.stages(directory)
    if not available:
        st.error("No finished jobs under this directory.", icon=":material/error:")
        st.stop()
    stage = st.selectbox("Stage", available)

    # Everything below sees only this window. It defaults to the whole period
    # the plan ran, and is keyed by run and stage so each starts at its own.
    span = runs.period(str(directory), stage)
    if span:
        picked = st.date_input("Date range", value=span, min_value=span[0],
                               max_value=span[1], format="YYYY-MM-DD",
                               key=f"dates-{directory}-{stage}")
        if len(picked) != 2:
            st.info("Pick an end date.", icon=":material/date_range:")
            st.stop()
        start, end = picked
    else:
        start, end = pd.Timestamp.min, pd.Timestamp.max

found = runs.jobs(str(directory), stage)
dark = getattr(getattr(st.context, "theme", None), "type", "light") == "dark"


def curve(job, selling_weekday: int | None = None) -> pd.DataFrame:
    """A job's equity inside the selected date range — or, given a weekday,
    only the P&L made on that weekday."""
    frame = series.window(runs.equity(str(job.path)), start, end)
    return frame if selling_weekday is None else series.selling_day(frame, selling_weekday)


# Axes whose table can be ranked on the selling day's P&L alone. In the
# delta-hedging sweep the hedge mode applies on every day but only the selling
# day's constant is swept, so the days a position is merely held run with a
# constant from a different mode's scale; ranking on the selling day sidesteps
# them until the sweep applies the constant to every day.
SELLING_DAY_AXES = {"delta_hedging"}
BASES = ["Full period", "Selling day only"]
BASIS_NOTE = ("Selling day only: P&L, drawdown and Sortino use just the days the "
              "variant sells on, leaving out the days its book is only held.")


st.title("Analyse run", icon=":material/insights:")
range_note = f" · {start} → {end}" if span else ""
st.caption(f"`{directory.name}` · {stage} · {len(found)} jobs{range_note}")
st.divider()

# Views are per axis per weekday. Any levels between the axis and the weekday
# (delta-hedge mode, condor leg method) are alternatives for the same choice,
# so they are pooled into one ranking and shown as a column, not a drill-down.
group_of = runs.groups(str(directory), stage)


def level_name(depth: int, grouped: bool) -> str:
    return (["Group", "Axis", "Weekday"] if grouped else ["Axis", "Weekday"])[depth]


def split(axis: str) -> tuple[str, str, str]:
    """A branch tag as (axis, the levels between, weekday)."""
    parts = axis.split("/")
    return parts[0], "/".join(parts[1:-1]), parts[-1]


def prefixed(job) -> str | None:
    if job.axis == runs.CONTROL:
        return None
    root, _, weekday = split(job.axis)
    group = group_of.get(root)
    return "/".join(([group] if group else []) + [root, weekday])


routes = sorted({route for route in (prefixed(job) for job in found) if route})
if not routes:
    st.info("This stage has only a control run.", icon=":material/info:")
    st.stop()

config_of = runs.configs(str(directory), stage)


def variants(root: str, weekday: str):
    """The variants of one axis x weekday, with the baseline they compare against.

    Returns (choices, branch_of, base, is_base): choices maps a unique label to
    its job, branch_of maps it to (mode or method, label), and is_base holds the
    label(s) that are the weekday's baseline.
    """
    in_axis = [job for job in found
               if job.axis != runs.CONTROL and split(job.axis)[::2] == (root, weekday)]
    base = baseline.find(found, config_of, weekday)
    choices, branch_of = {}, {}
    for job in in_axis:
        middle = split(job.axis)[1]
        label = f"baseline · {job.label}" if base and job.digest == base.digest else job.label
        key = f"{middle} · {label}" if middle else label
        choices[key], branch_of[key] = job, (middle, label)
    if base and all(job.digest != base.digest for job in in_axis):
        key = f"baseline · {weekday}"
        choices[key], branch_of[key] = base, ("", key)
    is_base = {k for k, job in choices.items() if base and job.digest == base.digest}
    return choices, branch_of, base, is_base


scope = f"{directory}/{stage}"
view_mode = st.segmented_control("View", ["Detail", "Summary", "Combine"], default="Detail",
                                 key=f"view-{scope}")
if view_mode in ("Summary", "Combine"):
    days = {}
    for job in found:
        if job.axis != runs.CONTROL:
            root_, _, day = split(job.axis)
            days.setdefault(root_, set()).add(day)
    roots = sorted(days, key=lambda r: (group_of.get(r, ""), r))
    weekdays_of = {r: [w for w in baseline.WEEKDAYS if w in days[r]] for r in roots}
    if view_mode == "Combine":
        st.subheader("1 · Pick each axis's winner", icon=":material/tune:")
    # In Combine each axis sets its own n: how many of its best variants carry
    # into the combinations. The plain summary always shows one winner.
    winners = summary.render(scope, roots, weekdays_of, variants, config_of, curve,
                             SELLING_DAY_AXES, BASES, BASIS_NOTE,
                             choose_n=view_mode == "Combine")
    if view_mode == "Combine":
        st.divider()
        st.subheader("2 · Combine them", icon=":material/merge:")
        stage_dir = Path(directory) / stage
        strategy = json.loads((stage_dir / runs.PLAN_FILE).read_text()).get("strategy", "")
        combination_page.render(
            stage_dir, scope, strategy, winners,
            [w for w in baseline.WEEKDAYS if any(w in d for d in weekdays_of.values())],
            lambda weekday: baseline.find(found, config_of, weekday),
            lambda path: series.window(runs.equity(str(path)), start, end))
    st.stop()

grouped = bool(group_of)
chosen_levels: list[str] = []
with st.container(horizontal=True, gap="medium"):
    while True:
        depth = len(chosen_levels)
        options = sorted({route.split("/")[depth] for route in routes
                          if route.split("/")[:depth] == chosen_levels
                          and len(route.split("/")) > depth})
        if not options:
            break
        picked = st.segmented_control(
            level_name(depth, grouped), options, default=options[0],
            # Keyed by the path above it, so changing a parent starts this
            # level fresh instead of holding a value that no longer exists.
            key=f"level-{depth}-{'/'.join(chosen_levels)}",
        )
        if not picked:
            st.stop()
        chosen_levels.append(picked)
        routes = [route for route in routes
                  if route.split("/")[:len(chosen_levels)] == chosen_levels]

# Scoped by run and stage too: the same axis/weekday exists in every stage, and a
# shared widget key would carry one stage's selection into another.
axis = "/".join([str(directory), stage] + (chosen_levels[1:] if grouped else chosen_levels))

# Each branch is compared against the baseline for its own weekday.
root, weekday = chosen_levels[-2], chosen_levels[-1]
choices, branch_of, base, is_base = variants(root, weekday)

shown = st.multiselect("Panels", list(panels.PANELS), default=list(panels.PANELS),
                       key="panels")

selling = None
if root in SELLING_DAY_AXES:
    if st.segmented_control("P&L basis", BASES, default=BASES[0],
                            key=f"{axis}:basis") == BASES[1]:
        selling = baseline.WEEKDAYS.index(weekday)
        st.caption(BASIS_NOTE)

# Filters narrow the variants before anything is ranked, so ranks and the
# composite are recomputed within what passes. The baseline always stays in.
candidates = {k: config_of[job.digest] for k, job in choices.items()
              if k not in is_base and job.digest in config_of}
day_key = str(baseline.WEEKDAYS.index(weekday))
passing = filters.render(axis, candidates, {k: branch_of[k][0] for k in candidates},
                         {k: day_key for k in candidates})
choices = {k: job for k, job in choices.items() if k in passing or k in is_base}
if not passing:
    st.info("No variants match these filters — showing the baseline alone.",
            icon=":material/filter_alt_off:")

# The table is an overview of every variant that passes the filters, so it
# renders ahead of the variant picker rather than being filtered by it.
margins = {label: margin.margin_factor(config_of[job.digest])
           for label, job in choices.items() if job.digest in config_of}
all_frames = {label: curve(job, selling) for label, job in choices.items()}
all_frames = {label: frame for label, frame in all_frames.items() if not frame.empty}
reference = margin.margin_factor(config_of[base.digest]) if base else 1.0
ranked = list(ranking.table(all_frames, margins, reference)["Variant"]) if all_frames else []
if "Ranking" in shown and all_frames:
    table_view = panels.View(axis=axis, frames=all_frames,
                             colours=charts.colours(list(all_frames), dark), dark=dark,
                             margins=margins, reference=reference, branches=branch_of,
                             baseline=next(iter(is_base), None))
    with st.container(border=True):
        st.subheader("Ranking", icon=":material/query_stats:")
        panels.PANELS["Ranking"](table_view)

st.divider()
# Default to the table's best few plus the baseline — as many as the line
# charts can colour — rather than every pooled variant.
default = [k for k in ranked if k not in is_base][:charts.MAX_LINES - 1]
default += [k for k in ranked if k in is_base]
# Keyed by the filtered set, so changing a filter resets the picker to the new
# top few instead of holding variants that no longer pass.
filtered = hashlib.sha256("\n".join(sorted(choices)).encode()).hexdigest()[:12]
chosen = st.multiselect("Variants", list(choices), default=default,
                        key=f"vars-{axis}-{filtered}")

if not chosen:
    st.info("Select at least one variant.", icon=":material/info:")
    st.stop()

frames = {label: curve(choices[label], selling) for label in chosen}
empty = [label for label, frame in frames.items() if frame.empty]
for label in empty:
    st.warning(f"No equity data for **{label}** in the selected date range.",
               icon=":material/warning:")
frames = {label: frame for label, frame in frames.items() if not frame.empty}
if not frames:
    st.stop()

view = panels.View(axis=axis, frames=frames,
                   colours=charts.colours(list(frames), dark), dark=dark)

for name in shown:
    if name == "Ranking":
        continue
    with st.container(border=True):
        st.subheader(name, icon=":material/query_stats:")
        panels.PANELS[name](view)
