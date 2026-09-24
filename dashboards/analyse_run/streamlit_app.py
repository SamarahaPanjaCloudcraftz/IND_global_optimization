"""Analyse a run.

    streamlit run dashboards/analyse_run/streamlit_app.py

Takes a run directory and nothing else. Everything shown is read from what the
sweep wrote there — the plan, the per-job results, the equity curves — so this
page never needs to agree with the planning dashboard about anything.

The page itself is deliberately thin: pick a stage, an axis, some variants, some
panels, and hand each panel the selection. What the panels are lives in
`panels.py`; adding one does not touch this file.
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import charts
import panels
import runs

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

found = runs.jobs(str(directory), stage)
dark = getattr(getattr(st.context, "theme", None), "type", "light") == "dark"

st.title("Analyse run", icon=":material/insights:")
st.caption(f"`{directory.name}` · {stage} · {len(found)} jobs")

# Charts live at a terminal branch: walk down through the sum levels and stop
# where the tree stopped being a sum. The levels come from the branch tag the
# plan wrote, prefixed by the group it recorded — this page infers no structure
# of its own.
WEEKDAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}

group_of = runs.groups(str(directory), stage)


def level_name(depth: int, options: list[str], grouped: bool) -> str:
    fixed = ["Group", "Axis"] if grouped else ["Axis"]
    if depth < len(fixed):
        return fixed[depth]
    return "Weekday" if set(options) <= WEEKDAYS else "Branch"


def prefixed(job) -> str | None:
    if job.axis == runs.CONTROL:
        return None
    group = group_of.get(job.axis.split("/")[0])
    return f"{group}/{job.axis}" if group else job.axis


routes = sorted({route for route in (prefixed(job) for job in found) if route})
if not routes:
    st.info("This stage has only a control run.", icon=":material/info:")
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
            level_name(depth, options, grouped), options, default=options[0],
            # Keyed by the path above it, so changing a parent starts this
            # level fresh instead of holding a value that no longer exists.
            key=f"level-{depth}-{'/'.join(chosen_levels)}",
        )
        if not picked:
            st.stop()
        chosen_levels.append(picked)
        routes = [route for route in routes
                  if route.split("/")[:len(chosen_levels)] == chosen_levels]

axis = "/".join(chosen_levels[1:] if grouped else chosen_levels)

# The control belongs to every axis, so it is offered alongside the axis's own
# variants rather than being a separate thing to go and find.
in_axis = [job for job in found if job.axis == axis]
control = [job for job in found if job.axis == runs.CONTROL] if axis != runs.CONTROL else []
choices = {f"{job.label}": job for job in in_axis}
choices.update({f"control · {job.label}": job for job in control})

with st.container(horizontal=True):
    chosen = st.multiselect("Variants", list(choices), default=list(choices),
                            key=f"vars-{axis}")
    shown = st.multiselect("Panels", list(panels.PANELS), default=list(panels.PANELS),
                           key="panels")

if not chosen:
    st.info("Select at least one variant.", icon=":material/info:")
    st.stop()

frames = {label: runs.equity(str(choices[label].path)) for label in chosen}
empty = [label for label, frame in frames.items() if frame.empty]
for label in empty:
    st.warning(f"No equity data for **{label}** — it wrote no consolidated store.",
               icon=":material/warning:")
frames = {label: frame for label, frame in frames.items() if not frame.empty}
if not frames:
    st.stop()

view = panels.View(axis=axis, frames=frames,
                   colours=charts.colours(list(frames), dark), dark=dark)

for name in shown:
    with st.container(border=True):
        st.subheader(name, icon=":material/query_stats:")
        panels.PANELS[name](view)
