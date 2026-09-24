# Running a sweep

## Contents

- [The shape of a run](#the-shape-of-a-run)
- [1. Plan](#1-plan)
- [2. Start](#2-start)
- [3. Monitor](#3-monitor)
- [4. Failures and resume](#4-failures-and-resume)
- [5. Analyse](#5-analyse)
- [6. Carry a winner forward](#6-carry-a-winner-forward)
- [Without the dashboard](#without-the-dashboard)
- [Stopping a sweep](#stopping-a-sweep)
- [Files a sweep writes](#files-a-sweep-writes)

## The shape of a run

```
outputs/2026-09-24_10-15-00/          <- run: one dated directory
  run.json                            <- run-level roll-up across stages
  stage1/
    plan.json                         <- the readable record of what was planned
    plan.pickle                       <- what the detached runner loads back
    status.json                       <- live per-job progress
    run_plan.log                      <- the runner process's own output
    control/<hash>/                   <- the baseline job
    dow_signal_strength/Monday/<hash>/
    delta_hedging/gamma_iv/Friday/<hash>/
    …
  stage2/                             <- next round, baseline = stage1's winner
```

A **stage** is one round of sweeps. Run a stage's axes, decide what to carry
forward, then plan the next stage with that config as its baseline.

## 1. Plan

In the planning dashboard (`dashboards/plan_and_start/streamlit_app.py`):

**Sidebar** — strategy, run directory, stage, backtest period, unit size, trade
interval, minutes-per-backtest (estimate only), and a Baseline expander showing
every baseline parameter as editable text. The sidebar also confirms the runner
resolved (`Runs through tradelib_runner:run_one`) or warns if it did not — a bad
`config.toml` shows up here.

If a sweep is already unfinished anywhere under `outputs/`, the page opens on
that run and stage rather than a fresh empty one.

**Main area** — pick axes (grouped: Hedge optimisation / Sell optimisation),
then per branch set each parameter's sweep. Closed parameters render as pills;
open ones as min / max / how many. A parameter offered "shared" is set once and
fanned out to every branch of that axis, with a toggle to drop back to
per-branch.

**Before starting**, the page shows:

- **Backtests to run** — the post-dedup count. This is the real number.
- **Before dedup** — everything the tree emits, plus the control.
- **Estimated time** — job count × minutes-per-backtest.
- **Jobs table** — one row per job: its axis, what changes from the baseline,
  and its directory digest.

Sanity-check the job count against what you expect before confirming. If a
sweep of five values shows one job, something is pinned or gated; if a weekday's
offsets look wrong, check the unwind constraint.

## 2. Start

Toggle the confirmation (it states the count and the estimate), then **Start
run**. "Write plan" saves `plan.json` + `plan.pickle` without starting anything
— useful for reviewing or for starting from a shell later.

The sweep runs **detached**: `start_detached` spawns `run_plan.py` with
`start_new_session=True`. That is not a nicety. Streamlit reruns its script on
every widget interaction, so an in-process loop would die at its next `st.*`
call — a click, a restart or a closed tab would each end a sweep midway.

You can close the browser, restart the dashboard, or drop the SSH session. The
sweep keeps going. It does not survive a reboot; resume after one.

## 3. Monitor

The dashboard's status panel is a `@st.fragment(run_every="5s")` reader of
`status.json`, so it shows the live state of a sweep it did not start — from any
machine that can see the file.

From a shell:

```bash
python - <<'PY'
import json
s = json.load(open("outputs/<run>/<stage>/status.json"))
print(f"{s['finished']}/{s['total']} done, {s['failed']} failed, updated {s['updated']}")
for j in s["jobs"][-5:]:
    print(j["ok"], j["axis"], j["seconds"], j["detail"][:80])
PY

tail -f outputs/<run>/<stage>/run_plan.log
```

**A hung runner looks exactly like a slow one** — both show a live pid.
`status["updated"]` is the tell: it is rewritten after every attempt, so a
timestamp far older than one backtest's duration means stuck, not busy.

`is_running(pid)` is what distinguishes "still working" from "crashed"; without
it both are simply unfinished.

## 4. Failures and resume

A job that raises is **recorded, not propagated** — one bad config must not cost
the rest of an overnight sweep. Its `run.log` holds the captured pipeline output.

`MAX_ATTEMPTS = 2`. One retry covers a crash mid-job; a config the engine
genuinely rejects would only burn the same time again. A job is **settled** when
it succeeded or when it has used both attempts.

Resume — the dashboard button, or:

```bash
./gopt_env/bin/python dashboards/plan_and_start/run_plan.py \
    outputs/<run>/<stage>/plan.pickle --resume
```

On a resume:

- settled jobs are skipped
- everything else runs again — **including a job that was mid-flight when the
  process died**, which leaves no status entry at all
- a job's directory is **deleted before it re-runs**. A backtest killed part way
  leaves some days written and no `result.json`, and the combiner would
  otherwise chain those stale days into the new run.

Resume runs **the plan that was stored**, not whatever the dashboard is
currently showing. To change the plan, plan a new stage.

`plan.pickle` exists because JSON cannot round-trip these configs — no integer
dict keys, no dates. `plan.json` is the record; the pickle is the source of
truth for the runner.

## 5. Analyse

```bash
./gopt_env/bin/streamlit run dashboards/analyse_run/streamlit_app.py
```

It takes a **run directory and nothing else**. It imports nothing from `engine`,
`strategies` or `tradelib`, and it writes nothing. Everything it shows is read
from what the sweep wrote there — the plan, the per-job results, the equity
curves — so it never needs to agree with the planning dashboard about anything.

This boundary is deliberate and worth preserving: the planning dashboard
produces a plan and results; analysis just reads whatever is there and shows it.
When analysis needs to know something structural — which axis belongs to which
group — the answer is to have the **plan record it** (`groups` in `plan.json`),
not to have analysis import the strategy module, which may have been renamed or
changed since the run.

Drill-down is cascading: Group → Axis → Weekday/Branch, landing at a terminal
branch. Panels (`panels.py`, a `@panel("Name")` registry): Summary, Equity,
Drawdown, P&L bars, Difference. Adding a panel does not touch the page.

- Equity comes from `consolidated_store/`, which the combiner has already
  offset-chained into one continuous cumulative curve. Its last value equals
  `result.json`'s `final_pnl` by construction.
- Variant labels are relative to the **axis**, not the control — within a branch
  most of the config is identical by construction, so a label against the
  control would bury the one parameter that actually varies.
- The control is offered alongside every axis's own variants, because it belongs
  to all of them.
- At most 8 lines are drawn at once; hues are never cycled. Beyond that it says
  how many are hidden.

## 6. Carry a winner forward

Selection is a **suggestion**, and today it is entirely manual: read the charts,
pick the winner, and paste its config into the next stage's Baseline expander.
The jobs table and `plan.json` both give a job's full config.

Do **not** use `engine.select.compose` for this yet — it replaces dict values
wholesale, so per-weekday winners would overwrite each other rather than
merging per key.

## Without the dashboard

Everything the dashboard does is in `plan_io.py`; nothing there imports
Streamlit. A sweep can be planned and started from a shell:

```python
import sys; sys.path.insert(0, ".")
sys.path.insert(0, "dashboards/plan_and_start")
import plan_io as P
from strategies import discover

strategy = discover()["NIFTY short vol DOW condor"]

plan = P.build_plan(
    strategy=strategy.name,
    tree=strategy.tree,
    baseline=strategy.baseline,          # edit before passing, if needed
    sweeps={                             # keyed by LEAF PATH
        "dow_signal_strength/Monday/unwind_time": P.spread(
            *strategy.unwind_time_range),
        "dow_signal_strength/Monday/unwind_trading_days_before": [0, 1],
    },
    only=["dow_signal_strength"],        # which axes to run
    run="outputs/2026-09-24_manual",
    stage="stage1",
    runner=strategy.runner,
    groups=strategy.groups,
)
print(plan.size)                          # check before committing
P.start_detached(plan)                    # writes plan.json + plan.pickle, then runs
```

Useful pieces: `P.spread(low, high, count)` builds a range;
`P.dict_product(per_key)` expands per-weekday ranges into whole dict values;
`strategy.ranges` gives the defaults keyed by leaf path.

To run in the foreground instead (blocking, output to the terminal):

```python
for i, job, ok, detail in P.launch(plan, P.load_run_one(plan.runner)):
    print(i, ok, job.axis, detail)
```

## Stopping a sweep

There is no stop button. Kill the runner pid from `status.json`:

```bash
kill $(python -c "import json;print(json.load(open('outputs/<run>/<stage>/status.json'))['pid'])")
```

The job that was mid-flight leaves a partial directory and no status entry; a
later `--resume` deletes that directory and runs it again. Killing the runner
does not kill an already-launched backtest subprocess immediately — check for a
stray `eis_env/bin/python` before restarting.

Afterwards the engine's constants file still holds that job's values. See
`backtest-engine.md`.

## Files a sweep writes

| File | Written by | Holds |
|---|---|---|
| `<stage>/plan.json` | `write_plan` | strategy, runner, groups, axes, sweeps, baseline, size, every job's axis/hash/dir/config |
| `<stage>/plan.pickle` | `write_plan` | the same plan, round-trippable |
| `<stage>/status.json` | `launch`, after every attempt, atomically | pid, started, updated, total, finished, failed, and per job: hash, axis, ok, attempts, detail, seconds |
| `<run>/run.json` | `launch` | per-stage totals and a run-level `complete` flag |
| `<stage>/run_plan.log` | `start_detached` | the runner process's stdout/stderr |
| `<job>/run.log` | `run_one` | the two pipeline scripts' output for that job |
