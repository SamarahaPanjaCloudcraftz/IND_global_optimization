"""The combine view: backtest combinations of the summary's axis winners and
read back which one to recommend for each weekday.

Rounds follow combine.py — round 1 runs H and S, round 2 runs F. Each round is
a plan written from here and executed by run_plan.py, whose command is shown
once the plan exists. Anything that already has a backtest doing the same thing
is reused rather than run again. A section only appears once every result it
compares exists, so no best-of is ever read off a half-finished round.
"""

import json
import re
import shutil
from collections.abc import Callable
from datetime import time
from pathlib import Path

import pandas as pd
import streamlit as st

import combine as C
import margin
import ranking
import series
from summary import FORMATS, METRICS, _against

WEEKDAY_KEYS = {0, 1, 2, 3, 4}


@st.cache_data(show_spinner=False)
def _stage(stage_dir: str, plan_mtime: float):
    """Native configs and axis-owned keys, re-read when the stage plan changes."""
    return C.native_configs(Path(stage_dir)), C.owned_keys(Path(stage_dir))


def _describe(contrib: dict, day: int) -> str:
    """A contribution as `key=value` pairs, weekday-keyed values at `day` only."""
    if not contrib:
        return "no change from the baseline"
    parts = []
    for key in sorted(contrib):
        value = contrib[key]
        if isinstance(value, dict) and WEEKDAY_KEYS <= set(value):
            value = value[day]
        if isinstance(value, time):
            value = value.strftime("%H:%M")
        elif isinstance(value, dict) and "type" in value:
            value = value["type"] if value.get("value") is None else f"{value['type']} {value['value']:g}"
        parts.append(f"{key}={value}")
    return ", ".join(parts)


def render(stage_dir: Path, scope: str, strategy: str, winners: dict, weekdays: list[str],
           base_of: Callable, equity_of: Callable) -> None:
    """`base_of(weekday)` is the weekday's baseline job; `equity_of(path)` a
    backtest directory's equity inside the selected date range."""
    natives, owned = _stage(str(stage_dir), (stage_dir / "plan.json").stat().st_mtime)
    index = C.results_index(stage_dir, natives)

    st.caption("Combinations of the winners picked above, per weekday. H = delta + gamma "
               "winners, S = condor + signal-strength + trade-time winners, F = best hedge "
               "+ best sell. Best-of steps rank on the full period, with the baseline in "
               "every comparison. P&L and drawdown are in ₹ on the baseline's margin.")

    def result(candidate: C.Candidate) -> Path | None:
        return index.get(C.effective_hash(candidate.config))

    def winner(root: str, weekday: str, base_config: dict) -> C.Candidate:
        pick = winners.get(root, {}).get(weekday)
        if not pick or pick[1]:
            label = "baseline" if pick else "baseline (no variant matched the filters)"
            return C.Candidate(root, label, {}, C.compose(base_config, {}))
        job, _, label = pick
        contrib = C.contribution(root, natives[job.digest], owned)
        return C.Candidate(root, label, contrib, C.compose(base_config, contrib))

    def combined(name: str, parts: list[C.Candidate], base_config: dict) -> C.Candidate:
        contrib = {k: v for part in parts for k, v in part.contrib.items()}
        label = " + ".join(f"{p.name}: {p.label}" for p in parts)
        return C.Candidate(name, label, contrib, C.compose(base_config, contrib),
                           [p.name for p in parts])

    def ranked(candidates: list[C.Candidate], base_config: dict, basis: str):
        """(full table, best row, baseline row) over candidates with results."""
        frames, margins = {}, {}
        for c in candidates:
            path = result(c)
            if path is not None:
                frames[c.name] = equity_of(path)
                margins[c.name] = margin.margin_factor(C.as_json(c.config))
        frames = {k: f for k, f in frames.items() if not f.empty}
        reference = margin.margin_factor(C.as_json(base_config))
        table = ranking.table(frames, margins, reference)
        return table, ranking.best(table, basis), table[table["Variant"] == "baseline"]

    def row_of(weekday: str, picked: C.Candidate, top: pd.Series, base_row: pd.DataFrame) -> dict:
        row = {"Weekday": weekday, "Pick": picked.name,
               "Changes from baseline": _describe(picked.contrib, WEEKDAYS.index(weekday))}
        for name in METRICS:
            row[name] = (_against(top[name], base_row.iloc[0][name], FORMATS[name])
                         if len(base_row) else FORMATS[name].format(top[name]))
        return row

    # ---- candidates per weekday
    plan = {}
    for weekday in weekdays:
        base_job = base_of(weekday)
        if base_job is None or base_job.digest not in natives:
            continue
        base_config = C.compose(natives[base_job.digest], {})
        axis = {root: winner(root, weekday, base_config) for root in C.HEDGE_AXES + C.SELL_AXES}
        hedge = combined("H", [axis[r] for r in C.HEDGE_AXES], base_config)
        sell = combined("S", [axis[r] for r in C.SELL_AXES], base_config)
        baseline = C.Candidate("baseline", "baseline", {}, base_config)
        plan[weekday] = dict(base=base_config, axis=axis, H=hedge, S=sell, baseline=baseline)

    # ---- round 1
    with st.container(border=True):
        st.subheader("Round 1 — hedge and sell combinations", icon=":material/merge:")
        rows, wanted = [], []
        for weekday, p in plan.items():
            rows.append({"Weekday": weekday,
                         "H = delta + gamma": p["H"].label,
                         "H": "✓ has a result" if result(p["H"]) else "to run",
                         "S = condor + signal + time": p["S"].label,
                         "S": "✓ has a result" if result(p["S"]) else "to run"})
            for kind in ("H", "S"):
                if not result(p[kind]):
                    wanted.append((f"{weekday}/{'hedge' if kind == 'H' else 'sell'}", p[kind].config))
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        _controls(stage_dir, scope, strategy, "round1", wanted)
    if any(not result(p[k]) for p in plan.values() for k in ("H", "S")):
        st.info("The best-of step and round 2 appear once every round 1 backtest has a result.",
                icon=":material/hourglass_top:")
        return

    # ---- best of each group
    with st.container(border=True):
        st.subheader("Best of each group", icon=":material/workspace_premium:")
        with st.container(horizontal=True, gap="large"):
            hedge_by = st.segmented_control("Hedge best by", ranking.TOP_BY, default="Composite",
                                            key=f"{scope}:combine:hedge:by") or "Composite"
            sell_by = st.segmented_control("Sell best by", ranking.TOP_BY, default="Composite",
                                           key=f"{scope}:combine:sell:by") or "Composite"
        groups = {"Hedge": ("H", C.HEDGE_AXES, hedge_by), "Sell": ("S", C.SELL_AXES, sell_by)}
        for title, (kind, roots, basis) in groups.items():
            rows, full = [], {}
            for weekday, p in plan.items():
                pool = [p["axis"][r] for r in roots] + [p[kind], p["baseline"]]
                table, top, base_row = ranked(pool, p["base"], basis)
                chosen = next(c for c in pool if c.name == top["Variant"])
                p[f"{kind}*"] = chosen
                rows.append(row_of(weekday, chosen, top, base_row))
                full[weekday] = table
            st.markdown(f"**{title}** — best of {', '.join(roots)}, {kind} and the baseline")
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            with st.expander(f"Every {title.lower()} candidate, per weekday"):
                for weekday, table in full.items():
                    st.caption(weekday)
                    st.dataframe(table.style.format({**FORMATS, **{c: "{:g}" for c in table.columns
                                                                   if c.endswith("rank") or c == "Composite"}}),
                                 hide_index=True, width="stretch")

    # ---- round 2
    with st.container(border=True):
        st.subheader("Round 2 — best hedge + best sell", icon=":material/merge:")
        rows, wanted = [], []
        for weekday, p in plan.items():
            p["F"] = combined("F", [p["H*"], p["S*"]], p["base"])
            rows.append({"Weekday": weekday, "Best hedge": p["H*"].name, "Best sell": p["S*"].name,
                         "F": "✓ has a result" if result(p["F"]) else "to run"})
            if not result(p["F"]):
                wanted.append((f"{weekday}/final", p["F"].config))
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        _controls(stage_dir, scope, strategy, "round2", wanted)
    if any(not result(p["F"]) for p in plan.values()):
        st.info("The recommendation appears once every round 2 backtest has a result.",
                icon=":material/hourglass_top:")
        return

    # ---- recommendation
    with st.container(border=True):
        st.subheader("Recommendation", icon=":material/verified:")
        st.caption("The best of nine, ranked together: each axis's winner, the combined "
                   "hedge H, the combined sell S, F (best hedge + best sell) and the baseline.")
        final_by = st.segmented_control("Best by", ranking.TOP_BY, default="Composite",
                                        key=f"{scope}:combine:final:by") or "Composite"
        rows, full, picks = [], {}, {}
        for weekday, p in plan.items():
            named = {**{FINAL_NAMES[r]: p["axis"][r] for r in C.HEDGE_AXES + C.SELL_AXES},
                     "combined hedge (H)": p["H"], "combined sell (S)": p["S"],
                     "F (best hedge + best sell)": p["F"], "baseline": p["baseline"]}
            pool = [C.Candidate(name, c.label, c.contrib, c.config) for name, c in named.items()]
            table, top, base_row = ranked(pool, p["base"], final_by)
            chosen = next(c for c in pool if c.name == top["Variant"])
            rows.append(row_of(weekday, chosen, top, base_row))
            full[weekday] = table
            picks[weekday] = chosen
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        if picks:
            risk_by = st.segmented_control("Risk column", list(RISK_MEASURES), default=RISK_DEFAULT,
                                           key=f"{scope}:combine:csv-risk") or RISK_DEFAULT
            # The same margin normalisation as the table: each variant put on its
            # weekday baseline's margin, so the CSV carries the numbers shown above.
            factors = {}
            for w, c in picks.items():
                own = margin.margin_factor(C.as_json(c.config))
                factors[w] = margin.margin_factor(C.as_json(plan[w]["base"])) / own if own else 0
            sheet = _margin_desk_sheet(picks, {w: equity_of(result(c)) for w, c in picks.items()},
                                       risk_by, factors)
            underlying = next(iter(picks.values())).config.get("underlying", "strategy")
            folder = stage_dir / C.COMBINATIONS / MARGIN_CSVS
            default = (f"margin_desk_{underlying}_{re.sub(r'[^0-9A-Za-z]+', '_', final_by.replace('&', '')).strip('_')}"
                       f"_{RISK_MEASURES[risk_by]}")
            with st.container(horizontal=True, vertical_alignment="bottom"):
                name = st.text_input("CSV name", value=default,
                                     key=f"{scope}:combine:csv-name:{final_by}:{risk_by}")
                save = st.button("Save CSV", key=f"{scope}:combine:csv-save", icon=":material/save:")
            if save and _save_margin_csv(sheet, folder, name):
                _write_recommendation({w: result(c) for w, c in picks.items()},
                                      stage_dir / C.COMBINATIONS / RECOMMENDATION)
            st.caption(f"Saves the margin desk strategies to {folder}, and copies each "
                       f"weekday's selected run into {stage_dir / C.COMBINATIONS / RECOMMENDATION}"
                       "/<weekday>, replacing what an earlier save put there. "
                       "One weekly strategy per weekday, in the margin desk's upload format. "
                       "lots marks the days each position is held; margin_0dte scales the "
                       "reference 0 DTE margin (NIFTY: 260 units every 5 min = $1.2M; SENSEX: 40 "
                       "units every 3 min = $1M) by the run's units ÷ trade interval; "
                       "expected_return is its total P&L in the selected date range, and risk "
                       "the size of its max drawdown or the standard deviation of its daily "
                       "P&L on the days it has P&L in that range, as chosen above, and "
                       "daily_pnl its P&L per day in that range — all on the "
                       "baseline's margin, exactly as in the table. current_margin and the "
                       "min / max margins are left at 0, to be filled in on the desk.")
        with st.expander("All nine candidates, per weekday"):
            ranks = {c: "{:g}" for c in next(iter(full.values())).columns
                     if c.endswith("rank") or c == "Composite"} if full else {}
            for weekday, table in full.items():
                st.caption(weekday)
                st.dataframe(table.style.format({**FORMATS, **ranks}), hide_index=True,
                             width="stretch")


FINAL_NAMES = {"delta_hedging": "best delta hedge", "gamma_hedging": "best gamma hedge",
               "condor_OTM_outstrike": "best condor", "dow_signal_strength": "best unwind",
               "trade_time": "best entry time"}

MARGIN_CSVS = "margin_csvs"
RECOMMENDATION = "recommendation"
# Risk column choices for the margin desk CSV -> the suffix naming the file.
RISK_MEASURES = {"Max drawdown": "maxdd", "Daily P&L std": "dailystd"}
RISK_DEFAULT = "Max drawdown"


def _save_margin_csv(sheet: pd.DataFrame, folder: Path, name: str) -> bool:
    """Write the sheet as <folder>/<name>.csv, never replacing an existing file.
    Returns whether it was written."""
    stem = name.strip()
    if stem.lower().endswith(".csv"):
        stem = stem[:-4].strip()
    if not stem or any(ch in stem for ch in "/\\") or stem.startswith("."):
        st.error("Enter a file name without slashes, e.g. margin_desk_NIFTY_Composite.",
                 icon=":material/error:")
        return False
    path = folder / f"{stem}.csv"
    if path.exists():
        st.error(f"{path.name} already exists in {folder} — choose another name.",
                 icon=":material/error:")
        return False
    folder.mkdir(parents=True, exist_ok=True)
    sheet.to_csv(path, index=False)
    st.success(f"Saved {path}", icon=":material/check_circle:")
    return True


def _write_recommendation(runs: dict, folder: Path) -> None:
    """Copy each weekday's selected run to <folder>/<weekday>/<run directory>.

    One folder holding the latest save's picks: a weekday already holding that
    run is left alone, one holding a different run has it replaced. Only the
    copies under <folder> are ever removed, never the runs they came from.
    """
    for weekday, source in runs.items():
        if source is None:
            st.warning(f"{weekday}: the selected variant has no result to copy.",
                       icon=":material/warning:")
            continue
        source = Path(source)
        target = folder / weekday
        if (target / source.name).is_dir() and len(list(target.iterdir())) == 1:
            continue
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        shutil.copytree(source, target / source.name)
    st.success(f"Recommendation runs in {folder}", icon=":material/folder_copy:")


# The margin desk's index codes, and the columns of its upload file, in its order.
DESK_INDEX = {"NIFTY": "NF", "BANKNIFTY": "BN", "SENSEX": "SN"}
# 0 DTE margin at a reference size, per index: (unit_size, trade interval in
# minutes, margin in $). Margin scales with units ÷ interval, as on the desk.
DESK_REF_MARGIN = {"NF": (260, 5, 1_200_000), "SN": (40, 3, 1_000_000)}


def _margin_0dte(config: dict, index: str) -> float:
    """0 DTE margin for a run's unit size and trade interval, from the reference."""
    if index not in DESK_REF_MARGIN:
        return 0
    units, minutes, dollars = DESK_REF_MARGIN[index]
    return round(dollars * (config["unit_size"] / units) * (minutes / config["trade_interval_time"]), 2)
DESK_COLUMNS = ["strategy", "index", "type", "margin_0dte", "min_margin", "max_margin",
                "lots", "current_margin", "expected_return", "risk", "daily_pnl"]


def _held_days(config: dict, day: int) -> str:
    """Mon..Fri pattern of the days a book sold on `day` is open: from the sale to
    its unwind, unwind_trading_days_before trading days ahead of expiry, on the
    nominal weekly cycle."""
    to_expiry = (config["expiry_day_of_week"] - day) % 5
    held = {(day + k) % 5 for k in range(to_expiry - config["unwind_trading_days_before"] + 1)}
    return "".join("1" if d in held else "0" for d in range(5))


def _risk(equity: pd.DataFrame, measure: str) -> float:
    """The risk figure for the desk, as a positive amount."""
    if equity.empty:
        return 0
    if measure == "Max drawdown":
        return round(float(-series.drawdown(equity)["drawdown"].min()), 2)
    # Days with no P&L are days the book is not open; counting them would
    # understate the spread of a variant that holds briefly.
    daily = series.pnl(equity, "Daily")["pnl"]
    daily = daily[daily != 0]
    return round(float(daily.std()), 2) if len(daily) > 1 else 0


def _margin_desk_sheet(picks: dict, equity: dict, risk_by: str = RISK_DEFAULT,
                       factors: dict | None = None) -> pd.DataFrame:
    """One margin-desk strategy row per weekday's recommended variant.

    expected_return is the total P&L of the range; risk is chosen by `risk_by`;
    daily_pnl is a JSON {date: P&L} of every day in the range, the change in
    end-of-day portfolio value, so it sums to expected_return. All three are
    multiplied by the weekday's margin factor (baseline margin ÷ the variant's),
    which scales a P&L series and so its drawdown and its standard deviation
    alike.
    The lot pattern is written with a leading apostrophe: the desk reads files
    through SheetJS, which would otherwise turn "00110" into the number 110 and
    shift the days, and the desk strips non-digits from the column anyway.
    """
    rows = []
    for weekday, chosen in picks.items():
        day = WEEKDAYS.index(weekday)
        index = DESK_INDEX.get(str(chosen.config.get("underlying", "")).upper(), "NF")
        scale = (factors or {}).get(weekday, 1.0)
        curve = equity[weekday].assign(equity=equity[weekday]["equity"] * scale)
        rows.append({
            "strategy": f"{str(chosen.config.get('underlying', index)).upper()}_{weekday[:3].upper()}",
            "index": index, "type": "weekly",
            "margin_0dte": _margin_0dte(chosen.config, index), "min_margin": 0, "max_margin": 0,
            "lots": "'" + _held_days(chosen.config, day),
            "current_margin": 0,
            "expected_return": round(float(curve["equity"].iloc[-1]), 2) if not curve.empty else 0,
            "risk": _risk(curve, risk_by),
            "daily_pnl": json.dumps({f"{day:%Y-%m-%d}": round(float(value), 2) for day, value
                                     in series.pnl(curve, "Daily").itertuples(index=False)}),
        })
    return pd.DataFrame(rows, columns=DESK_COLUMNS)


WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def _controls(stage_dir: Path, scope: str, strategy: str, name: str,
              wanted: list[tuple[str, dict]]) -> None:
    """Write a round's plan and show how to run it, with its last status."""
    status = C.round_status(stage_dir, name)
    running = bool(status and C.P.is_running(status.get("pid"))
                   and status.get("finished", 0) < status.get("total", 0))
    pickle_path = C.round_dir(stage_dir, name) / C.P.PLAN_PICKLE

    unique = {}
    for tag, config in wanted:
        unique.setdefault(C.effective_hash(config), (tag, config))
    wanted = list(unique.values())

    if not wanted:
        st.success("Nothing to run — every combination in this round already has a backtest.",
                   icon=":material/check_circle:")
    else:
        planned = set()
        if pickle_path.exists():
            planned = {C.effective_hash(job.config) for job in C.P.read_plan(pickle_path).jobs}
        current = set(unique) <= planned
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown(f"**{len(wanted)}** backtest(s) to run.")
            if running:
                st.warning("This round is running now.", icon=":material/sync:")
            elif st.button(f"Write {name} plan", key=f"{scope}:combine:{name}:write",
                           type="primary" if not current else "secondary"):
                C.write_round(stage_dir, name, strategy, wanted)
                current = True
        if current:
            st.caption("Plan written. Start it from a terminal:")
            st.code(C.command(pickle_path), language="bash")
    if status:
        st.caption(f"Last run: {status.get('finished', 0)}/{status.get('total', 0)} finished, "
                   f"{status.get('failed', 0)} failed · updated {status.get('updated', '—')}")
