# Setting up on a new machine or server

Two separate Python environments are involved and they must not be confused.

| | Optimisation engine | Backtest engine |
|---|---|---|
| Lives in | `./gopt_env` (project root) | the tradelib repo's `eis_env` |
| Python | 3.13 | 3.9 |
| Needs | stdlib + streamlit (+ pandas/altair via streamlit) | the full tradelib dependency set |
| Runs | trees, plans, dashboards, the sweep driver | the backtests themselves |

The optimisation engine's core is pure standard library. Streamlit is there only
for the two dashboards. Backtests are never run in `gopt_env` — `run_one` shells
out to the other interpreter.

## 1. Get both repositories onto the machine

- This project: `global_optimisation_engine/`
- The backtest engine: `HFT-Options-EIS-Global/` (tradelib)

They are independent checkouts. Nothing in this project assumes they are
siblings on disk; `config.toml` is what connects them.

## 2. Build the optimisation environment

```bash
cd /path/to/global_optimisation_engine
conda env create -f environment.yml -p ./gopt_env
```

The env lives **in the project root**, deliberately — not as a named conda env.
`environment.yml` pins `python=3.13` and pip-installs `streamlit`; everything
else arrives as a streamlit dependency.

Verify:

```bash
./gopt_env/bin/python -V            # 3.13.x
./gopt_env/bin/python -c "import streamlit, pandas, altair; print('ok')"
```

## 3. Make sure the backtest engine runs on its own

Before wiring anything up, confirm the backtest engine works standalone on this
machine — its env exists, its data directories are present and populated, and a
single hand-run backtest completes. If it cannot run by itself, a sweep will
simply produce dozens of identical failures.

Things that are machine-specific inside the backtest engine and are **not**
managed by this project:

- `eis_env` (or whatever interpreter you point `config.toml` at) and its packages
- the preprocessed data directories, which the strategy classes name in their
  `data_dir` attribute. They are **not under a common parent** — today NIFTY
  points at `/…/IND_short_vol/preproc_data/NIFTY/` and SENSEX at
  `/…/IND_backtest/SENSEX/`, so check both
- anything the engine reads by absolute path

If the data lives elsewhere on the server, update `data_dir` in
`strategies/nifty_short_vol_dow_condor.py` /
`strategies/sensex_short_vol_dow_condor.py`. It is part of the baseline, so it
is pinned into every job's config and written into every run's snapshot.

## 4. Edit `config.toml` — the one machine-specific file

```toml
[backtest_engine]
repo = "/absolute/path/to/HFT-Options-EIS-Global"
python = "eis_env/bin/python"     # absolute, or relative to `repo`
```

This is read at import of `tradelib_runner`, and both paths are checked for
existence. A bad path therefore fails immediately — at import, at dashboard
start-up, before any job runs — rather than part-way through a long sweep. The
planning dashboard surfaces it as a warning in the sidebar and refuses to enable
the Start button.

Check it:

```bash
./gopt_env/bin/python -c "
import tradelib_runner as R
print(R.REPO); print(R.PYTHON); print(R.CONSTANTS); print(R.PIPELINE)"
```

`R.CONSTANTS` must be the real `tradelib/tradelib_global_constants.py` — that is
the file every run rewrites.

## 5. Take a snapshot of the constants file

Because every run mutates it in place and the last job's values are left behind,
keep a pristine copy before the first sweep:

```bash
cp "$REPO/tradelib/tradelib_global_constants.py" \
   "$REPO/tradelib/tradelib_global_constants.py.pristine"
```

Do **not** restore it with `git checkout` — that repository carries the owner's
own uncommitted work. Restore by copying the snapshot back, or by editing the
specific lines.

## 6. Smoke-test one job end to end

Run a single control job before committing to a large sweep. This exercises
every moving part: the tree, the formatter, the rewrite, both subprocesses, and
the output layout.

It **rewrites the engine's constants file** — take the snapshot in step 5 first,
and do not run it while any other backtest is in flight against the same
checkout.

```bash
./gopt_env/bin/python - <<'PY'
import sys; sys.path.insert(0, ".")
from pathlib import Path
from strategies import discover
from engine import Job
import tradelib_runner as R

strategy = discover()["NIFTY short vol DOW condor"]
job = Job(config=strategy.baseline, axis=None)
out = Path("outputs/smoketest/stage0/control")
print(R.run_one(job, out))
PY
```

Then check, in that directory:

- `result.json` exists and holds a `final_pnl`
- `tradelib_global_constants.py` (the snapshot) matches what you asked for
- `consolidated_store/` has CSVs
- `run.log` shows both pipeline scripts finishing

A period shorter than the strategy's default makes this quick — edit `period` on
the class, or overlay `start_date`/`end_date` on the config in the snippet.

## 7. Start the dashboards

```bash
./gopt_env/bin/streamlit run dashboards/plan_and_start/streamlit_app.py --server.port 8502
./gopt_env/bin/streamlit run dashboards/analyse_run/streamlit_app.py    --server.port 8503
```

On a headless server, tunnel rather than bind publicly:

```bash
ssh -N -L 8502:localhost:8502 -L 8503:localhost:8503 user@server
```

Then open `http://localhost:8502`. If you must bind to an interface, put it
behind something that authenticates — the planning dashboard can start days of
compute and the analysis dashboard exposes P&L.

For fully headless operation with no dashboard at all, see
`running-a-sweep.md` § "Without the dashboard".

## 8. Long sweeps

A full sweep can run for days. The sweep process is already detached
(`start_new_session=True`), so it survives a closed tab and a dropped SSH
session. It does **not** survive a reboot — on a resume, `run_plan.py --resume`
picks up from `status.json`.

Practical checks before starting a multi-day run:

- disk: each job writes a full results directory (blotter, portfolio,
  consolidated store, logs). Multiply by the job count.
- the machine's sleep/idle settings
- that nothing else on the machine will run a backtest against the same
  checkout of the engine while the sweep is going
