"""Reading a run directory.

Knows about the layout a sweep writes and nothing else - no charts, no page.
The unit is a Job: one backtest, its config's distinguishing values, and its
end-of-day equity curve.

Equity comes from `consolidated_store/`, which the backtest combiner has already
offset-chained into one continuous cumulative curve across the whole period. Its
last value is exactly what `result.json` reports as `final_pnl`, so anything
derived here reconciles with the engine's own number by construction.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st

PLAN_FILE = "plan.json"
CONTROL = "control"


@dataclass(frozen=True)
class Job:
    axis: str        # "control", or a branch path like "dow_signal_strength/Monday"
    digest: str      # the directory name, a hash of the whole config
    path: Path
    final_pnl: float
    label: str       # what distinguishes this job inside its axis


def stages(run: Path) -> list[str]:
    """Stage directories under a run, in order."""
    return sorted(d.name for d in Path(run).iterdir()
                  if d.is_dir() and any(d.rglob("result.json")))


@st.cache_data(show_spinner=False)
def jobs(run: str, stage: str) -> list[Job]:
    """Every finished job in a stage, labelled by what sets it apart."""
    root = Path(run) / stage
    changes = _changes(root)
    found = []
    for result in sorted(root.rglob("result.json")):
        directory = result.parent
        axis = str(directory.parent.relative_to(root))
        found.append(Job(
            axis=axis,
            digest=directory.name,
            path=directory,
            final_pnl=json.loads(result.read_text())["final_pnl"],
            label=changes.get(directory.name) or directory.name,
        ))
    return found


def _changes(stage_root: Path) -> dict[str, str]:
    """digest -> a label naming what sets this job apart inside its own axis.

    Relative to the axis, not to the control. Within one branch most of the
    config is identical by construction — the branch pins it — so a label
    against the control would be dominated by what the branch already tells
    you, burying the one parameter that actually varies.

    Read from plan.json, which is a record rather than a round-trippable
    source: dict keys arrive as strings and dates as text. Fine for labels.
    """
    plan = stage_root / PLAN_FILE
    if not plan.exists():
        return {}
    entries = json.loads(plan.read_text())["jobs"]
    control = next((e["config"] for e in entries if e["axis"] is None), {})

    grouped = defaultdict(list)
    for entry in entries:
        grouped[entry["axis"]].append(entry)

    labels = {}
    for group in grouped.values():
        if len(group) > 1:
            varies = [key for key in group[0]["config"]
                      if len({json.dumps(e["config"].get(key), sort_keys=True, default=str)
                              for e in group}) > 1]
        else:
            varies = [key for key, value in group[0]["config"].items()
                      if control.get(key) != value]
        for entry in group:
            named = ", ".join(f"{key}={entry['config'][key]}" for key in varies)
            labels[entry["hash"]] = named or "baseline"
    return labels


@st.cache_data(show_spinner=False)
def groups(run: str, stage: str) -> dict[str, str]:
    """axis -> the group the plan put it in.

    Empty for runs planned before the plan recorded groups, in which case the
    drill-down simply starts at the axis. Nothing here reconstructs the tree:
    what the plan did not write down, this dashboard does not know.
    """
    plan = Path(run) / stage / PLAN_FILE
    if not plan.exists():
        return {}
    recorded = json.loads(plan.read_text()).get("groups") or {}
    return {axis: group for group, members in recorded.items() for axis in members}


def axes(found: list[Job]) -> list[str]:
    """Axis tags present, control last - it belongs to every axis, not one."""
    tags = sorted({job.axis for job in found if job.axis != CONTROL})
    return tags + ([CONTROL] if any(job.axis == CONTROL for job in found) else [])


@st.cache_data(show_spinner=False)
def equity(job_path: str) -> pd.DataFrame:
    """End-of-day equity for one job, as (date, equity).

    Daily rather than per-minute: the curve is ~18k rows a job at source and
    every view here is daily or coarser.
    """
    files = sorted(Path(job_path).glob("consolidated_store/*.csv"))
    if not files:
        return pd.DataFrame(columns=["date", "equity"])
    frames = [
        pd.read_csv(f, usecols=["timestamp", "portfolio_value"], parse_dates=["timestamp"])
        for f in files
    ]
    rows = pd.concat(frames, ignore_index=True).dropna(subset=["portfolio_value"])
    rows["date"] = rows["timestamp"].dt.normalize()
    daily = rows.groupby("date", as_index=False)["portfolio_value"].last()
    return daily.rename(columns={"portfolio_value": "equity"})
