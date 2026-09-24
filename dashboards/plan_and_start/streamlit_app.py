"""Plan a sweep and start it.

    streamlit run dashboards/plan_and_start/streamlit_app.py

The strategy's tree is the universe: it fixes which parameters may be optimized
and which of them interact. This page only selects within it - which axes to
run, and what values each parameter sweeps.

A closed parameter is shown as pills, because the tree fixes what exists. An
open one is a multiselect that accepts new options, because no fixed set of
values could ever cover what you might want to try.

Launching goes through a `run_one` you name by import path. Nothing here knows
what a backtest is.
"""

from datetime import date, datetime, time
from pathlib import Path

import streamlit as st

import plan_io as P  # also puts the engine package on sys.path

from engine import alternatives, axes, leaves, paths
from strategies import discover

st.set_page_config(
    page_title="Plan and start",
    page_icon=":material/account_tree:",
    layout="wide",
)

STRATEGIES = discover()
if not STRATEGIES:
    st.error(
        "No strategies found. Add a module under `strategies/` exposing `TREE` and `BASELINE`.",
        icon=":material/error:",
    )
    st.stop()

# ---------------------------------------------------------------- run settings

_resume = P.unfinished_run()
st.session_state.setdefault(
    "run",
    _resume[0] if _resume
    else str(P.ROOT / "outputs" / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")),
)
st.session_state.setdefault("stage", _resume[1] if _resume else "stage1")

with st.sidebar:
    st.subheader("Run", icon=":material/tune:")
    name = st.selectbox("Strategy", sorted(STRATEGIES))
    strategy = STRATEGIES[name]
    tree = strategy.tree

    run = st.text_input("Run directory", key="run")
    stage = st.text_input(
        "Stage", key="stage",
        help="Run one set of axes, decide what to carry forward, then plan the "
             "next stage against that as its baseline.",
    )
    runner_spec = getattr(strategy, "runner", None)
    runner, runner_error = None, None
    if not runner_spec:
        runner_error = f"{name} declares no `RUNNER`."
    else:
        try:
            runner = P.load_run_one(runner_spec)
        except Exception as error:  # noqa: BLE001 - shown, not swallowed
            runner_error = f"`{runner_spec}` — {type(error).__name__}: {error}"
    period = st.date_input(
        "Backtest period",
        value=(strategy.baseline["start_date"], strategy.baseline["end_date"]),
        key=f"period_{name}",
        help="Common to every run in the sweep.",
    )

    unit_size = st.number_input(
        "Unit size", min_value=1, step=1,
        value=int(strategy.baseline["unit_size"]),
        key=f"unit_{name}", help="Common to every run in the sweep.",
    )
    trade_interval = st.number_input(
        "Trade interval, minutes", min_value=1, step=1,
        value=int(strategy.baseline["trade_interval_time"]),
        key=f"interval_{name}", help="Common to every run in the sweep.",
    )

    minutes = st.number_input(
        "Minutes per backtest", min_value=0.1, value=5.0, step=0.5,
        help="Only used for the time estimate.",
    )

    if runner:
        st.caption(f"Runs through `{runner_spec}`")
    else:
        st.warning(runner_error, icon=":material/warning:")

    with st.expander("Baseline", icon=":material/flag:"):
        st.caption(
            "Every job is this config with one axis's parameters overlaid. For a "
            "later stage, paste the config you chose to carry forward."
        )
        baseline, bad_baseline = {}, []
        for key, value in strategy.baseline.items():
            if key in ("start_date", "end_date", "unit_size", "trade_interval_time"):
                continue  # set by their own controls above
            typed = st.text_input(key, value=P.render_value(value), key=f"base_{name}_{key}")
            try:
                baseline[key] = P.parse_value(typed, value)
            except ValueError as error:
                baseline[key] = value
                bad_baseline.append(f"`{key}`: {error}")

if not isinstance(period, tuple) or len(period) != 2:
    st.warning("Pick both ends of the backtest period.", icon=":material/date_range:")
    st.stop()
baseline["start_date"], baseline["end_date"] = period
baseline["unit_size"] = int(unit_size)
baseline["trade_interval_time"] = int(trade_interval)

# ---------------------------------------------------------------- axes

@st.fragment(run_every="5s")
def run_status(stage_directory) -> dict | None:
    """Progress of whatever is running in this stage, read from status.json.

    The sweep runs in its own process, so this is just a reader: restarting the
    dashboard, or opening it on another machine, shows the same live state.
    """
    status = P.read_status(stage_directory)
    if not status:
        return None
    finished, total = status["finished"], status["total"]
    failed = status.get("failed", sum(1 for job in status["jobs"] if not job["ok"]))
    incomplete = finished < total
    alive = P.is_running(status.get("pid"))

    if incomplete and alive:
        title, icon = "Sweep in progress", ":material/monitoring:"
    elif incomplete:
        title, icon = "Sweep stopped before finishing", ":material/pause_circle:"
    else:
        title, icon = "Last sweep in this stage", ":material/history:"

    with st.container(border=True):
        st.subheader(title, icon=icon)
        st.progress(finished / total if total else 0.0,
                    text=f"{finished} of {total} finished"
                         + (f" · {failed} failed" if failed else ""))
        st.dataframe(
            [{"job": job["axis"] or "control",
              "state": "ok" if job["ok"] else "failed",
              "tries": job.get("attempts", 1),
              "seconds": job["seconds"],
              "detail": job["detail"]} for job in reversed(status["jobs"])],
            hide_index=True, height=200,
            column_config={
                "job": st.column_config.TextColumn("Job", width="medium"),
                "state": st.column_config.TextColumn("State", width="small"),
                "tries": st.column_config.NumberColumn("Tries", width="small"),
                "seconds": st.column_config.NumberColumn("Seconds", width="small", format="%.0f"),
                "detail": st.column_config.TextColumn("Output", width="large"),
            },
        )
        if incomplete and alive:
            st.caption(f"Running as process {status['pid']} — closing or restarting "
                       "this page will not stop it.")
        elif incomplete:
            st.caption(f"Stopped at {finished} of {total}. Resume picks up from "
                       "there: finished jobs are skipped, and a job that failed "
                       f"{P.MAX_ATTEMPTS} times is settled and left alone.")
    return status


st.title("Plan and start", icon=":material/account_tree:")
st.caption(
    f"{name} — the tree fixes which parameters exist and which interact. "
    "Select within it: choose axes, then the values each parameter sweeps."
)

_status = P.read_status(Path(run) / stage)
run_status(Path(run) / stage)
_incomplete = bool(_status and _status["finished"] < _status["total"])
_alive = bool(_status and P.is_running(_status.get("pid")))
in_progress = _incomplete and _alive

if _incomplete and not _alive:
    # Resume runs the plan that was stored, not whatever the page is showing
    # now: a plan rebuilt from changed selections would hash differently and
    # count as an entirely different set of jobs.
    _stored = Path(run) / stage / P.PLAN_PICKLE
    if _stored.exists():
        if st.button(
            f"Resume — {_status['finished']} of {_status['total']} done",
            type="primary", icon=":material/resume:",
        ):
            _pid = P.start_detached(P.read_plan(_stored), resume=True)
            st.success(f"Resumed as process {_pid}.", icon=":material/rocket_launch:")
            st.rerun()
    else:
        st.warning("No plan.pickle in this stage, so it cannot be resumed.",
                   icon=":material/warning:")

for problem in bad_baseline:
    st.error(problem, icon=":material/error:")
if bad_baseline:
    st.stop()

groups = dict(strategy.groups or {})
pending = dict(strategy.pending or {})
shared = dict(strategy.shared or {})
baselines = dict(strategy.baselines or {})
varies = dict(strategy.varies or {})
ranges = dict(getattr(strategy, "ranges", None) or {})
by_name = {axis.name: axis for axis in axes(tree)}
owner = {leaf.name: axis.name for axis in axes(tree) for leaf in leaves(axis)}
ungrouped = [n for n in by_name if not any(n in names for names in groups.values())]
if ungrouped or not groups:
    groups.setdefault("Other", []).extend(ungrouped)

sweeps: dict = {}      # leaf path -> values
problems: list[str] = []
selected: list[str] = []


def entry_editor(leaf, path, weekday: int) -> None:
    """One entry of a dict-valued parameter, e.g. Monday's hedge constant.

    The config value is a whole dict, but only this weekday's entry varies -
    the rest stay at baseline, which is what makes the weekday its own axis.
    """
    base = baseline[leaf.name]
    chosen = range_editor(path, leaf.name, base[weekday], f"v_{name}_{path}",
                          label=f"`{leaf.name}` · entry {weekday}")
    sweeps[path] = [{**base, weekday: value} for value in chosen]


def range_default(path: str, parameter: str, like):
    """The (low, high, count) default for a leaf.

    Tried from the most specific key to the least: the exact leaf path, then
    shorter prefixes with the parameter appended, then the bare parameter name.
    So a range that is the same everywhere is stated once in the strategy, and
    one that differs per weekday is stated per weekday.
    """
    if path in ranges:
        return ranges[path]
    parts = path.split("/")
    for cut in range(len(parts) - 1, 0, -1):
        key = "/".join(parts[:cut] + [parameter])
        if key in ranges:
            return ranges[key]
    if parameter in ranges:
        return ranges[parameter]
    return (like, like, 1)


def range_editor(path: str, parameter: str, like, key: str, label: str = "") -> list:
    """From / to / how many, expanded to equally spaced values.

    Sweeps are ranges of a continuous quantity, so this is what the dashboard
    asks for. Setting the count to 1 pins the value at `from`.
    """
    low, high, count = range_default(path, parameter, like)
    if label:
        st.caption(label)
    with st.container(horizontal=True, gap="small"):
        if isinstance(low, time) or isinstance(like, time):
            start = st.time_input("from", value=low, step=60, key=f"{key}_lo")
            stop = st.time_input("to", value=high, step=60, key=f"{key}_hi")
        else:
            start = st.number_input("from", value=low, key=f"{key}_lo", format="%g")
            stop = st.number_input("to", value=high, key=f"{key}_hi", format="%g")
        how_many = st.number_input("values", min_value=1, max_value=500,
                                   value=int(count), step=1, key=f"{key}_n")
    values = P.spread(start, stop, how_many)
    st.caption(" · ".join(P.render_value(v) for v in values[:8])
               + (f" … {len(values)} values" if len(values) > 8 else ""))
    return values


def key_values(leaf, key: str) -> list[str]:
    """The distinct values of one key across a leaf's declared dicts."""
    return list(dict.fromkeys(P.render_value(value[key]) for value in leaf.values))


def editor(leaf, path, label: str | None = None) -> None:
    """One value editor for a leaf, addressed by its path in the tree."""
    key = varies.get(leaf.name)
    if key is not None and leaf.values and isinstance(leaf.values[0], dict):
        # The branch fixes the rest of the dict — its type — and only this key
        # varies, so the input holds bare values rather than whole dicts.
        base = leaf.values[0]
        chosen = range_editor(path, leaf.name, base[key], f"v_{name}_{path}",
                              label=label or f"`{leaf.name}` {key} · type = "
                                             f"{base.get('type')!r}")
        sweeps[path] = [{**base, key: value} for value in chosen]
        return
    if leaf.domain is not None:
        picked = st.pills(
            label or f"`{leaf.name}`", leaf.domain, selection_mode="multi",
            default=list(leaf.values), key=f"v_{name}_{path}",
            help="Closed parameter: the tree fixes what exists.",
        )
        sweeps[path] = list(picked)
        return
    sweeps[path] = range_editor(path, leaf.name, baseline[leaf.name],
                                f"v_{name}_{path}", label=label or f"`{leaf.name}`")


def shared_editors(node, base: str, parameters: list[str]) -> set[str]:
    """Axis-level settings. Returns the parameters handled here, not per branch.

    Every branch has its own leaf for these in the tree — sharing is only a
    front-end convenience, one input whose values are written to every branch.
    Turning a share off hands that parameter back to the branches, each with its
    own input, without the tree changing at all.

    A parameter the engine ignores unless another one is on is declared in the
    strategy's GATED map. With its gate off the input is hidden and the value
    pinned to what the engine actually uses, so the recorded config matches the
    run instead of carrying a number that was silently discarded.
    """
    gated = dict(strategy.gated or {})
    resolved: dict[str, list] = {}
    handled: set[str] = set()

    for parameter_name in parameters:
        relative = [rel for rel in paths(node)
                    if rel.rsplit("/", 1)[-1] == parameter_name]
        if not relative:
            continue
        sample = paths(node)[relative[0]]
        targets = [f"{base}/{rel}" for rel in relative]

        # A gate only applies where the gating parameter actually exists. The
        # delta hedge consults percent_hedge only when custom_pct_to_hedge is
        # on; the gamma component multiplies by it unconditionally and has no
        # such gate, so the rule must not follow the parameter name across axes.
        gate = gated.get(parameter_name)
        if gate and not any(rel.rsplit("/", 1)[-1] == gate[0] for rel in paths(node)):
            gate = None
        if gate and not (resolved.get(gate[0]) or [None])[0]:
            for path in targets:
                sweeps[path] = [gate[1]]
            st.caption(
                f"`{parameter_name}` is fixed at {gate[1]} because `{gate[0]}` is off — "
                "the engine ignores it either way."
            )
            handled.add(parameter_name)
            continue

        if sample.domain is not None and all(
                isinstance(value, bool) for value in sample.domain):
            on = st.toggle(
                f"`{parameter_name}`", value=bool(sample.values[0]),
                key=f"v_{name}_{base}_shared_{parameter_name}",
                help="Applies to every branch of this axis.",
            )
            resolved[parameter_name] = [on]
            for path in targets:
                sweeps[path] = [on]
            handled.add(parameter_name)
            continue

        together = st.toggle(
            f"One `{parameter_name}` for every branch", value=True,
            key=f"share_{name}_{base}_{parameter_name}",
            help=f"Off gives each of the {len(targets)} branches its own values.",
        )
        if not together:
            continue  # the branches render it themselves

        leaves_by_path = {f"{base}/{rel}": paths(node)[rel] for rel in relative}
        domains = [leaf.domain for leaf in leaves_by_path.values()]

        key = varies.get(parameter_name)
        if key is not None and isinstance(sample.values[0], dict):
            chosen = range_editor(
                f"{base}/{relative[0]}", parameter_name, sample.values[0][key],
                f"v_{name}_{base}_shared_{parameter_name}",
                label=f"`{parameter_name}` {key} · type = "
                      f"{sample.values[0].get('type')!r} · all {len(targets)} branches",
            )
            for path, leaf in leaves_by_path.items():
                own = leaf.values[0]
                sweeps[path] = [{**own, key: value} for value in chosen]
            handled.add(parameter_name)
            continue

        # A per-weekday dict shared across branches: the input holds the entry
        # values, and each weekday applies them to its own key rather than every
        # branch receiving the same whole dict.
        held = baseline.get(parameter_name)
        weekday_of = {path: P.weekday_index(path.split("/")[-2])
                      for path in leaves_by_path}
        if isinstance(held, dict) and all(day is not None for day in weekday_of.values()):
            first = sample.values[0][weekday_of[next(iter(leaves_by_path))]]
            chosen = range_editor(
                f"{base}/{relative[0]}", parameter_name, first,
                f"v_{name}_{base}_shared_{parameter_name}",
                label=f"`{parameter_name}` · applied to each of the "
                      f"{len(leaves_by_path)} weekdays on its own key",
            )
            for path in leaves_by_path:
                day = weekday_of[path]
                sweeps[path] = [{**held, day: value} for value in chosen]
            handled.add(parameter_name)
            continue

        if all(domain is not None for domain in domains):
            # Branches can have different legal sets. Offer their union, then
            # clamp each branch to its own — so one input still respects a
            # per-branch constraint, and the job count reflects it.
            union = list(dict.fromkeys(value for domain in domains for value in domain))
            # Default to what every branch declares, not to the first branch's
            # values: the branches have different legal sets, and seeding from
            # one of them would clamp all the others down to it.
            declared = list(dict.fromkeys(
                value for leaf in leaves_by_path.values() for value in leaf.values))
            chosen = list(st.pills(
                f"`{parameter_name}`", union, selection_mode="multi",
                default=declared,
                key=f"v_{name}_{base}_shared_{parameter_name}",
                help=f"Applied to all {len(targets)} branches, each keeping only "
                     "the values legal for it.",
            ))
        else:
            chosen = range_editor(
                f"{base}/{relative[0]}", parameter_name, baseline[parameter_name],
                f"v_{name}_{base}_shared_{parameter_name}",
                label=f"`{parameter_name}` · all {len(targets)} branches",
            )

        if chosen is not None:
            dropped = []
            for path, leaf in leaves_by_path.items():
                kept = [v for v in chosen if leaf.domain is None or v in leaf.domain]
                sweeps[path] = kept
                if len(kept) != len(chosen):
                    dropped.append(f"{path.split('/')[-2]} {kept}")
            if dropped:
                st.caption("Kept per branch: " + " · ".join(dropped))
        handled.add(parameter_name)

    return handled


def held_at_baseline(axis) -> None:
    """What every other parameter is set to while this axis runs."""
    mine = {leaf.name for leaf in leaves(axis)}
    rows = [
        {
            "parameter": key,
            "value": P.render_value(value),
            "swept by": owner.get(key, "— not an axis parameter"),
        }
        for key, value in baseline.items() if key not in mine
    ]
    with st.expander(f"Held at baseline ({len(rows)} parameters)", icon=":material/lock:"):
        st.caption(
            "Every run on this axis uses these values. Parameters belonging to another "
            "axis sit at baseline here — that is what makes each axis's result a "
            "measurement of that axis alone."
        )
        st.dataframe(
            rows, hide_index=True,
            column_config={
                "parameter": st.column_config.TextColumn("Parameter", width="medium"),
                "value": st.column_config.TextColumn("Held at", width="medium"),
                "swept by": st.column_config.TextColumn("Swept by", width="small"),
            },
        )


def weekday_block(children, prefix: str, skip: set[str], off: set[str] = frozenset(),
                  needs_baseline: tuple = (), scopes: dict | None = None) -> None:
    """Weekday branches: one labelled row of inputs per per-weekday parameter.

    Every parameter that varies by weekday gets its own row — named once above
    it, with the weekdays as columns — whether it is one entry of a dict, like a
    hedge constant, or a plain scalar, like a gamma threshold. What the branch
    pins is stated once for the block rather than repeated in every column.

    A parameter listed in `needs_baseline` also gets a row for what the *other*
    weekdays hold, because its non-swept keys still affect the run. A weekday's
    entry window is not one of those: with a one-hot signal no other day sells,
    so its other keys never apply and the config baseline stands.
    """
    live = [child for child in children if f"{prefix}{child.name}/" not in off]
    if not live:
        return

    def leaf_of(child, leaf_name):
        return next(leaf for leaf in leaves(child) if leaf.name == leaf_name)

    # What every branch here pins, said once.
    settings: dict[str, set[str]] = {}
    for child in live:
        for leaf in leaves(child):
            if leaf.domain and len(leaf.domain) == 1:
                settings.setdefault(leaf.name, set()).add(P.render_value(leaf.values[0]))
    if settings:
        st.caption(" · ".join(
            f"`{parameter}` = {next(iter(values))}" if len(values) == 1
            else f"`{parameter}` set per weekday"
            for parameter, values in settings.items()))

    editable = [leaf for leaf in leaves(children[0])
                if leaf.name not in skip and not (leaf.domain and len(leaf.domain) == 1)]
    if not editable:
        return

    for leaf in editable:
        by_entry = isinstance(baseline.get(leaf.name), dict) and leaf.name not in varies
        declared = leaf.values[0]
        held = dict(baseline[leaf.name]) if by_entry else None

        if by_entry and leaf.name in needs_baseline:
            st.caption(f"Baseline `{leaf.name}` — held on the weekdays not being swept.")
            for column, child in zip(st.columns(len(live)), live):
                weekday = P.weekday_index(child.name)
                with column:
                    typed = st.text_input(
                        child.name, value=P.render_value(declared[weekday]),
                        key=f"b_{name}_{prefix}{child.name}_{leaf.name}",
                    )
                    try:
                        held[weekday] = P.parse_value(typed, declared[weekday])
                    except ValueError as error:
                        problems.append(f"{child.name} baseline: {error}")

        st.caption(f"`{leaf.name}`")
        for column, child in zip(st.columns(len(live)), live):
            weekday = P.weekday_index(child.name)
            path = f"{prefix}{child.name}/{leaf.name}"
            with column:
                if by_entry:
                    chosen = range_editor(path, leaf.name, declared[weekday],
                                          f"v_{name}_{path}", label=f"**{child.name}**")
                    sweeps[path] = [{**held, weekday: value} for value in chosen]
                else:
                    editor(leaf_of(child, leaf.name), path, label=f"**{child.name}**")


def render(node, prefix: str, skip: set[str], off: set[str] = frozenset(),
           needs_baseline: tuple = (), scopes: dict | None = None) -> None:
    """Draw a subtree: a sum becomes labelled blocks, a product a row of editors.

    A shared setting is rendered at whichever node its scope names, so the same
    parameter can be shared across a whole axis in one tree and across just one
    group of weekdays in another.
    """
    if prefix in off:
        return
    scopes = scopes or {}
    here = scopes.get(prefix.rstrip("/"))
    if here:
        skip = set(skip) | shared_editors(node, prefix.rstrip("/"), here)
    children = alternatives(node)
    if children is None:
        editable = [leaf for leaf in leaves(node)
                    if leaf.name not in skip and not (leaf.domain and len(leaf.domain) == 1)]
        weekday = P.weekday_index(node.name)
        with st.container(horizontal=True, gap="medium"):
            for leaf in editable:
                path = f"{prefix}{leaf.name}"
                if weekday is not None and isinstance(baseline.get(leaf.name), dict):
                    entry_editor(leaf, path, weekday)
                else:
                    editor(leaf, path)
        return

    weekdays = [child for child in children if P.weekday_index(child.name) is not None]
    if weekdays and len(weekdays) == len(children):
        weekday_block(children, prefix, skip, off, needs_baseline, scopes)
        return

    for child in children:
        if f"{prefix}{child.name}/" in off:
            continue
        with st.expander(child.name, icon=":material/alt_route:", expanded=True):
            settings: dict[str, set[str]] = {}
            for leaf in leaves(child):
                if leaf.domain and len(leaf.domain) == 1:
                    settings.setdefault(leaf.name, set()).add(P.render_value(leaf.values[0]))
            if settings:
                st.caption(" · ".join(
                    f"`{parameter_name}` = {next(iter(values))}" if len(values) == 1
                    else f"`{parameter_name}` set per weekday"
                    for parameter_name, values in settings.items()
                ))
            render(child, f"{prefix}{child.name}/", skip, off, needs_baseline, scopes)


for group, axis_names in groups.items():
    st.subheader(group, icon=":material/category:")
    offered = [n for n in axis_names if n in by_name and n not in pending]
    chosen_axes = st.pills(
        f"{group} axes", offered, selection_mode="multi", default=offered,
        key=f"axes_{name}_{group}", label_visibility="collapsed",
    )
    selected += chosen_axes

    for axis_name in axis_names:
        if axis_name in pending:
            with st.container(border=True):
                st.markdown(f"**{axis_name}**")
                st.badge("pending", color="grey", icon=":material/block:")
                st.caption(pending[axis_name])
            continue
        if axis_name not in chosen_axes:
            continue

        axis = by_name[axis_name]
        axis_shared = shared.get(axis_name, [])
        with st.container(border=True):
            st.markdown(f"**{axis_name}**")
            group_count = len(branch_paths := [b for b, _ in P.branches_of(axis)])
            if group_count > 1:
                st.badge(f"{group_count} branches", color="orange",
                         icon=":material/alt_route:",
                         help="Each branch is stored and tagged separately.")
            elif len(leaves(axis)) > 1:
                st.badge(f"product · {len(leaves(axis))} parameters", color="violet",
                         icon=":material/join_inner:",
                         help="These parameters interact, so this axis is chosen as a "
                              "whole cell — never one parameter at a time.")
            all_branches = [path for path, _ in P.branches_of(axis)]
            live_branches = all_branches
            parts = [branch.split("/") for branch in all_branches]
            depths = {len(part) for part in parts}

            if len(all_branches) > 1 and len(depths) == 1 and all_branches[0]:
                # Branches here are a grid — method by weekday — so offer one
                # picker per level rather than one row of every full path.
                kept = []
                for level in range(depths.pop()):
                    options = list(dict.fromkeys(part[level] for part in parts))
                    label = ("Weekday" if all(P.weekday_index(o) is not None for o in options)
                             else ("Method" if level == 0 else f"Level {level + 1}"))
                    picker = st.pills if len(options) <= 8 else st.multiselect
                    extra = {"selection_mode": "multi"} if picker is st.pills else {}
                    kept.append(set(picker(
                        f"{axis_name} {label.lower()}", options, default=options,
                        key=f"br_{name}_{axis_name}_{level}",
                        help=f"{label}s to include in this run.", **extra,
                    )))
                live_branches = ["/".join(part) for part in parts
                                 if all(part[i] in kept[i] for i in range(len(part)))]
            switched_off = set(all_branches) - set(live_branches)
            off_prefixes = {f"{axis_name}/{path}/" for path in switched_off}

            scopes = {prefix: params for prefix, params in shared.items()
                      if prefix == axis_name or prefix.startswith(f"{axis_name}/")}
            render(axis, f"{axis_name}/", set(), off_prefixes,
                   tuple(baselines.get(axis_name, [])), scopes)

            # Applied last so it wins over the shared editors, which fan out to
            # every branch including the ones switched off. An empty leaf makes
            # its whole product generate nothing.
            for relative in paths(axis):
                branch = relative.rsplit("/", 1)[0] if "/" in relative else ""
                if branch in switched_off:
                    sweeps[f"{axis_name}/{relative}"] = []

            held_at_baseline(axis)

for problem in problems:
    st.error(problem, icon=":material/error:")
if problems:
    st.stop()
if not selected:
    st.info("Select at least one axis to plan a run.", icon=":material/info:")
    st.stop()

# ---------------------------------------------------------------- the plan

plan = P.build_plan(
    strategy=name, tree=tree, baseline=baseline, sweeps=sweeps,
    only=selected, run=run, stage=stage, runner=runner_spec or "",
    groups=groups,
)
estimate = plan.size.runs * minutes
estimate_text = f"{int(estimate // 60)}h {int(estimate % 60):02d}m"

with st.container(horizontal=True):
    st.metric(
        "Backtests to run", plan.size.runs, border=True, icon=":material/play_circle:",
        help="After collapsing the all-baseline config into one shared control run.",
    )
    st.metric(
        "Before dedup", plan.size.variants + 1, border=True,
        icon=":material/account_tree:",
        help="Everything the tree emits, plus the one control run.",
        delta=f"-{plan.size.deduped} deduped" if plan.size.deduped else None,
        delta_color="off",
    )
    st.metric("Estimated time", estimate_text, border=True, icon=":material/schedule:")

with st.container(border=True):
    st.subheader("Jobs", icon=":material/table_chart:")
    st.dataframe(
        [
            {
                "axis": job.axis or "control",
                "changes": ", ".join(
                    f"{key}={P.render_value(value)}"
                    for key, value in job.config.items()
                    if baseline[key] != value
                ) or "—",
                "directory": P.config_hash(job.config),
            }
            for job in plan.jobs
        ],
        hide_index=True,
        column_config={
            "axis": st.column_config.TextColumn("Axis", width="small"),
            "changes": st.column_config.TextColumn("Changes from baseline", width="large"),
            "directory": st.column_config.TextColumn(
                "Directory", width="small",
                help="A digest of the whole config, so the same config always lands "
                     "in the same directory name — in any axis, stage or run.",
            ),
        },
    )
    st.caption(f"Under `{plan.stage_dir()}/<axis>/`")

# ---------------------------------------------------------------- start

confirmed = st.toggle(
    f"Confirm {plan.size.runs} backtests, about {estimate_text}",
    disabled=runner is None or in_progress,
    help="A sweep is already running in this stage." if in_progress
         else ("Starting cannot be undone from here." if runner
               else "No runner to start — see the sidebar."),
)
with st.container(horizontal=True):
    write_clicked = st.button("Write plan", icon=":material/save:")
    start_clicked = st.button(
        "Start run", type="primary", icon=":material/play_arrow:",
        disabled=not confirmed or runner is None or in_progress,
    )

if write_clicked:
    st.toast(f"Wrote {P.write_plan(plan)}", icon=":material/save:")

if start_clicked:
    pid = P.start_detached(plan)
    st.success(
        f"Started {plan.size.runs} backtests as process {pid}. It runs on its own — "
        "you can close or restart this page.",
        icon=":material/rocket_launch:",
    )
    st.rerun()
