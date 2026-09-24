---
name: short-vol-optimisation
description: Operate the global optimisation engine for India short-vol options strategies (NIFTY / SENSEX day-of-week condor) — the project that plans parameter sweeps, drives the tradelib backtest engine (HFT-Options-EIS-Global) one config at a time, and analyses the results. Use this whenever the work involves this project's sweeps, axes, trees, baselines, run directories or dashboards: setting it up on a new machine or server, planning or starting a sweep, resuming one that stopped, reading or debugging a run directory, adding or changing an axis or a strategy, or interpreting the results. Use it even when the request never says "optimisation engine" — "start the NIFTY sweep", "why did these backtests take no trades", "add an axis for X", "the run died overnight, pick it back up" and "which weekday won" are all this skill.
---

# Global optimisation engine — short vol, India

This project decides **what to backtest**. It does not backtest anything itself:
it produces complete configs, hands them one at a time to the tradelib backtest
engine, and reads back what that engine wrote.

Project root (the only place anything for this lives):

```
/home/oem/Documents/unit_tasks/IND_short_vol/global_optimisation_engine
```

The backtest engine is a **separate repository** (`HFT-Options-EIS-Global`, also
called tradelib). This project never modifies it except for one file, and that
file is the whole integration — see [Traps](#traps-read-before-your-first-sweep).

## The five things to understand first

1. **The parameter space is a sum of products.** Sum children are *independent
   axes* — each is optimised on its own and the winners composed. Product
   children are *interacting parameters* — chosen as a whole cell, never one at
   a time. Everything in the code follows from this one shape.
2. **The strategy's tree is the universe.** A user picks *which axes to run* and
   *what values to sweep*, never new parameters. The tree is the author's
   hypothesis about what interacts; it is not a user setting.
3. **Every job is a complete config** — the baseline with one axis's parameters
   overlaid. There is exactly one **control** run (the pure baseline); each
   axis's all-baseline variant dedups into it.
4. **The backtest engine has no config API.** A run is set up by rewriting
   assignment lines in `tradelib/tradelib_global_constants.py` and launching
   subprocesses. That file is shared mutable state, so **runs are strictly
   sequential** and the last job's values are left behind afterwards.
5. **Selection is a suggestion, never automatic.** The engine ranks and shows;
   a human picks the winner and carries it forward as the next stage's baseline.
   `engine/select.py` exists but is not yet trusted — see
   [What is not built](#what-is-not-built-yet).

## Layout

```
engine/            the core, pure stdlib, knows nothing about backtests
  tree.py          Leaf, Internal, SUM, PRODUCT, paths(), branches(), sweep()
  plan.py          Job, build_jobs(), plan_size(), canonical()
  paths.py         config_hash(), job_dir()  — deterministic output layout
  runner.py        run_all(): map jobs through a caller-supplied run_one
  select.py        winner picking — present but NOT yet trustworthy
strategies/
  dow_condor.py    the shared shape: the whole tree, as overridable methods
  nifty_short_vol_dow_condor.py    values only
  sensex_short_vol_dow_condor.py   values only
tradelib_format.py   native values -> Python source for the constants file
tradelib_runner.py   rewrite the constants file, launch the pipeline
config.toml          THE ONLY MACHINE-SPECIFIC FILE — edit it on a new machine
dashboards/
  plan_and_start/    pick axes and sweep values; start/resume detached runs
  analyse_run/       read-only: takes a run directory and charts it
docs/                engine constraints, axis validation, deferred work
outputs/             <run>/<stage>/<axis path>/<config hash>/
gopt_env/            this project's Python 3.13 env (NOT the backtest env)
```

`tradelib_format.py` and `tradelib_runner.py` are the **only two files that know
the backtest engine exists**. Everything above them — tree, plan, dashboards —
never learns what a backtest is. Keep it that way when extending.

## Working on a new machine or server

Read `references/setup.md` before touching anything. The short version:

```bash
cd /path/to/global_optimisation_engine
conda env create -f environment.yml -p ./gopt_env      # python 3.13 + streamlit
$EDITOR config.toml                                    # <-- the server-specific step
./gopt_env/bin/python -c "import tradelib_runner"      # fails loudly on a bad path
```

`config.toml` names the backtest engine's repo and the interpreter that runs it
(`eis_env/bin/python`, Python 3.9). It is validated at import, so a wrong path
fails there rather than part-way through a three-day sweep.

Dashboards:

```bash
./gopt_env/bin/streamlit run dashboards/plan_and_start/streamlit_app.py --server.port 8502
./gopt_env/bin/streamlit run dashboards/analyse_run/streamlit_app.py    --server.port 8503
```

On a headless server, forward the ports (`ssh -L 8502:localhost:8502 …`) rather
than exposing them. A sweep can also be planned and started with **no dashboard
at all** — see `references/running-a-sweep.md`.

## The model

Read `references/tree-model.md` for the full picture: sum vs product, leaf
domains, path addressing, terminal branches, the dedup pass, deterministic
hashing, and the five axes of the current strategies with their branch counts.

The vocabulary you will need in any conversation about this project:

| Term | Meaning |
|---|---|
| **axis** | a child of the root sum; optimised independently (`gamma_hedging`, `delta_hedging`, `condor_OTM_outstrike`, `trade_time`, `dow_signal_strength`) |
| **branch** | a terminal group under an axis — walk down through sums, stop at the first non-sum. The unit of tagging, of directory layout, and of charting |
| **leaf path** | how a parameter is addressed, e.g. `delta_hedging/gamma/Monday/underlying_threshold_hedge_constant`. Many leaves share a name, so the path is the identity |
| **domain** | a leaf's legal set. `None` = open (user supplies values); a list = closed (pills); a single-value list = fixed, unsweepable |
| **baseline** | the complete config every job departs from |
| **control** | the single job that is exactly the baseline |
| **stage** | one round of sweeps. Run stage1's axes, decide, then plan stage2 with the chosen config as its baseline |

## Running a sweep

Read `references/running-a-sweep.md` for the full procedure, including the
programmatic path. The shape of it:

1. **Plan** — in the planning dashboard pick the strategy, run directory, stage,
   backtest period, unit size, then the axes and each parameter's sweep range
   (min / max / how many → equally spaced). The page shows the post-dedup job
   count and a time estimate before anything starts.
2. **Start** — confirm, then Start. The sweep is launched **detached**
   (`start_detached` → `run_plan.py`, `start_new_session=True`), so it survives
   the dashboard being closed, restarted, or the SSH session dropping.
3. **Monitor** — `<run>/<stage>/status.json` is the live record (per job: ok,
   attempts, seconds, detail) and `<run>/run.json` is the run-level roll-up. The
   dashboard reads these; so can you, with `cat`. A hung runner and a slow one
   both show a live pid — `status["updated"]` is the tell.
4. **Resume** — a stopped sweep is picked up with the Resume button or
   `run_plan.py <run>/<stage>/plan.pickle --resume`. Settled jobs (`ok`, or
   `attempts >= 2`) are skipped; anything else re-runs, and its directory is
   deleted first so a half-written backtest cannot be chained into the new one.
5. **Analyse** — the analysis dashboard takes a run directory and nothing else.

**One sweep at a time, per backtest-engine checkout.** Two concurrent sweeps
rewrite the same constants file and silently produce runs whose recorded config
is not what executed. If parallelism is ever needed, the answer is a second
checkout of the backtest engine with its own `config.toml`, not threads.

## Reading results

Each job directory holds what the backtest engine wrote:

```
<run>/<stage>/<axis>/<branch>/<config hash>/
  result.json                     {"final_pnl": ...}   ← the score
  consolidated_store/*.csv        the chained equity curve
  blotter/ portfolio/ backtest/ gamma_log/ log/
  tradelib_global_constants.py    the config snapshot for THIS run
  run.log                         captured pipeline output
```

The config snapshot is the only proof that the config that ran is the config
that was planned — diff it against `plan.json` when anything looks wrong. It is
copied by the backtest engine's own `backtest_combiner.py`, not by this project.

The directory name is a sha256 digest of the **complete** config, so the same
config lands in the same directory name in any axis, stage or run — which is
what makes results joinable across them.

## Traps — read before your first sweep

`docs/Backtest_engine_constraints.md` is the authoritative list;
`references/backtest-engine.md` explains the interaction end to end. The ones
that have actually bitten:

- **A parameter can silently do nothing.** `gamma_hege_otm_outstrike` (sic — the
  misspelling is load-bearing) is a **delta × 100**, not a strike distance.
  Swept as `50, 100, 150`, the last two match no option, the gamma hedge is
  skipped, and the run still completes with a plausible P&L and a config saying
  it was hedged. Assume every parameter has a version of this until proven
  otherwise — `docs/Axis_validation_plan.md` is the procedure.
- **An unwind earlier than the trade closes the book before it opens.** The
  legal offsets for a selling weekday are
  `range((expiry_weekday - weekday) % 5 + 1)`; anything larger produces a run
  that completes and takes zero trades. The tree enforces this as a domain.
- **`percent_hedge` is read by both hedging components, asymmetrically.** The
  gamma component multiplies by it unconditionally; the delta side only consults
  it when `custom_pct_to_hedge` is on, returning `1.0` otherwise.
- **`gamma_threshold` is assigned twice** in the constants file (lines 58 and
  299). `write_config`'s multiline regex rewrites both, which is what keeps them
  in step — accidentally.
- **`write_config` raises if a parameter has no module-level assignment.** That
  is deliberate: silence there would run the baseline value while labelling the
  output with the swept one.
- **Weekday branches work by a one-hot signal.** `day_of_week_signal_strength =
  {0:1, 1:0, …}` means only that weekday sells, which is what makes the branch a
  measurement of that day alone. Do not "fix" it to the multi-day baseline.

## Changing things

Read `references/extending.md` before adding an axis, a parameter, a strategy or
a dashboard panel. Two standing constraints from the project's owner:

- **No speculative features.** Build what was asked for; raise anything else and
  let it be decided.
- **Never revert the backtest engine repository with git.** It carries the
  owner's own uncommitted work. Any change there is surgical, flagged
  prominently *before* a sweep runs, and never committed without asking.

## What is not built yet

Do not present any of these as working:

- **`select.compose` replaces dicts wholesale**, so per-weekday winners would
  overwrite each other. It must be fixed to merge per key before any composed
  config is trusted.
- **Selection semantics** (choice vs independent sum) are undecided, and the
  `select` protocols are unwritten. Winner picking is done by a human reading
  the analysis dashboard.
- **The entry-window constraint** — an entry end at or before the start sells
  nothing — is not enforced in the UI.
- **Weekday-first delta hedging** is blocked on the backtest engine making
  `underlying_threshold_hedge_type` weekday-keyed. See
  `docs/Weekday_first_hedging.md`.
- **The `IVWAP` axis** is named in the optimisation table but has no constant,
  module or reference in the backtest engine; the tree reports it as pending.

## Reference files

| File | Read it when |
|---|---|
| `references/setup.md` | standing the project up on a new machine or server |
| `references/tree-model.md` | working with the tree, axes, branches, domains, hashing |
| `references/running-a-sweep.md` | planning, starting, monitoring, resuming, analysing |
| `references/backtest-engine.md` | anything touching tradelib, or a run behaving oddly |
| `references/extending.md` | adding or changing an axis, strategy, parameter or panel |

Project docs worth reading directly: `docs/Optimization_tree.md` (the original
design), `docs/Backtest_engine_constraints.md`, `docs/Axis_validation_plan.md`,
`docs/Weekday_first_hedging.md`.
