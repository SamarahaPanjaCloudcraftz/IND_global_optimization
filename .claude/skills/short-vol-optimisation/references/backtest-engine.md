# How this drives the tradelib backtest engine

Line numbers below were correct on 2026-09-24 and drift as the engine changes —
grep for the symbol rather than trusting the number.
`docs/Backtest_engine_constraints.md` in the project is the authoritative
write-up; this file is the operational view.

## Contents

- [The integration in one paragraph](#the-integration-in-one-paragraph)
- [What `run_one` does](#what-run_one-does)
- [Writing values as source](#writing-values-as-source)
- [Consequences of shared mutable state](#consequences-of-shared-mutable-state)
- [Parameters that lie](#parameters-that-lie)
- [Validating an axis before trusting it](#validating-an-axis-before-trusting-it)
- [Working inside the backtest repository](#working-inside-the-backtest-repository)

## The integration in one paragraph

The backtest engine has **no configuration API**. Its settings are module-level
globals in `tradelib/tradelib_global_constants.py`, imported by
`main_backtest_single_process.py` and read from there by everything downstream.
The only way to change what a run does is to **edit the text of that file** and
then launch. `tradelib_runner.write_config` rewrites one assignment line per
parameter; `run_one` then runs two scripts as subprocesses under the engine's own
interpreter. Everything else about this project's design is a consequence of
that one fact.

## What `run_one` does

`tradelib_runner.run_one(job, out_dir)`:

1. `out_dir.mkdir(parents=True, exist_ok=True)`
2. `write_config(as_assignments({**job.config, "output_dir_folder": str(out_dir)}))`
   — rewrites `^name\s*=.*$` for each parameter, `re.MULTILINE`
3. runs, in order, with `cwd=REPO` and `PYTHONPATH=REPO`:
   - `tradelib/main_backtest_single_process.py`
   - `tradelib/backtest_combiner.py`
4. captures both scripts' output to `<out_dir>/run.log`
5. raises `RuntimeError` on a non-zero exit, naming the log

The chartbook and chartpack steps of the engine's own pipeline are **deliberately
excluded**: they are presentation, they have a known failure, and a sweep of
dozens of runs has no use for per-run chart packs.

`write_config` **raises** if a parameter has no module-level assignment to
rewrite. Silence there would be the worst possible failure — the sweep would run
that job at the baseline value while labelling its output with the swept one. So
a typo in a parameter name fails loudly at the first job rather than producing a
whole sweep of mislabelled results.

The combiner writes `result.json` (`{"final_pnl": …}`) and copies the constants
file into the output directory as that run's config snapshot
(`backtest_combiner.py:172`). That snapshot is arranged by the **engine**, not by
this project, so it stops being true if that line changes.

## Writing values as source

`tradelib_format.as_source` turns a native Python value into source text that
still means the same thing once the constants file is imported.

The constants file's imports are `os, json, numpy as np` and
`from datetime import timedelta, date, time, datetime`. That is what makes the
bare `time(9, 17, 0)` and `date(2026, 1, 1)` forms correct.

**`repr()` is deliberately not used for dates and times.** It yields
`datetime.time(9, 17)`, and the file binds `datetime` to the *class*, not the
module — so such a line parses cleanly and then fails when the file is imported,
four subprocesses away from the mistake.

Every type gets an explicit rule and **anything unrecognised raises**. A wrong
guess produces source that parses and means something else, which is the one
failure mode worth engineering against here. `Raw(str)` is the escape hatch for a
value that must refer to another variable in the file; nothing checks it.

## Consequences of shared mutable state

There is one copy of the constants file, and a run is set up by mutating it in
place.

**Runs must be sequential.** Two backtests started concurrently would each
rewrite the same lines and both would read whichever write landed last.
Parallelism is not a tuning knob that can be turned on later — it is blocked by
the configuration mechanism, and any attempt at it silently produces runs whose
recorded config is not what executed. If parallelism is genuinely needed, the
answer is a second checkout of the engine with its own `config.toml`.

**The last job's values are left behind.** When a sweep finishes, the file holds
whatever the final job wrote. A manual backtest run afterwards silently inherits
it. The hand-written `trade_simulator_nifty.py` driver has always behaved this
way, so it is not a regression — but it is a standing trap that gets worse as
sweeps grow. Keep a `.pristine` copy (see `setup.md`) and restore by copying,
never with `git checkout`.

**Any parameter not pinned in the baseline is inherited from run history.** This
is why the strategy baseline pins a long list of settings that no axis sweeps —
fee rate, hedge flags, gamma hedge window, IV thresholds, `HARD_LIMIT` and so
on. Several of them differ between `trade_simulator_nifty.py` and
`trade_simulator_sensex.py`, so leaving them unpinned would make a run depend on
which simulator was used last.

**Why this is tolerable.** Every run writes its own config snapshot into its
output directory, so the record of what a run actually used is intact even after
the shared file moves on. The directory hash depends on the config, not on the
file.

**What a real fix looks like.** The engine accepts a config — a path to a per-run
settings file, or a dict passed to the driver — instead of importing globals.
That one change removes the sequential constraint, the residue problem, and the
need for `tradelib_format` to exist at all.

## Parameters that lie

The shared failure mode: **a swept parameter that quietly does nothing still
produces a complete results directory, a valid P&L, and a config snapshot naming
the swept value.** Nothing crashes and nothing warns. Selection then compares
that run against real ones and may crown it the winner.

### `gamma_hege_otm_outstrike` is a delta, not a strike distance

The misspelling (`hege`) is consistent across every file that references it and
there is no correctly-spelled variant, so the tree must spell it the same way or
`write_config` will refuse it.

In `tradelib/strategies/strategy_components/gamma_hedge_component.py`, the
`get_static_otm_option` path that would treat it as a strike distance is
**commented out** (lines 269, 274). The live path (lines 279, 284) passes it to
`get_delta_based_otm_option`, which computes `delta_outstrike / 100`. So the
value is a **delta in percent**: the baseline `50` means delta 0.50, an
at-the-money option — not 50 points out.

Swept as `50, 100, 150`, the last two are deltas of 1.0 and 1.5, which match no
option. The component logs critical and returns early; the gamma hedge never
happens. The backtest completes and writes a plausible P&L.

Sweep values belong in 0–100 delta terms. The real fix is a rename to
`gamma_hedge_delta_outstrike`; failing that, constrain the leaf's `domain`.

### `percent_hedge` is read by both hedging components, asymmetrically

- **Gamma side** — `gamma_hedge_component.py:291` multiplies the hedge ratio by
  `percent_hedge` **unconditionally**.
- **Delta side** — `hedge_component.py:109`'s `get_pct_to_hedge()` returns
  `percent_hedge` only `if custom_pct_to_hedge`, and `1.0` otherwise.

The two hedges are independent but write to the **same config key**. That is why
the tree gives the gamma axis its own default range
(`gamma_hedging/percent_hedge`) while both still write `percent_hedge`, and why
`gated = {"percent_hedge": ("custom_pct_to_hedge", 1)}` exists — on an axis where
`custom_pct_to_hedge` is present and off, the input is hidden and the value
pinned to `1`, so the recorded config says what the engine actually did.

**A gate only applies where its gating parameter exists in that node's paths.**
Applied globally it would pin `percent_hedge` on the gamma axis too, turning a
five-value sweep into five identical runs.

### `gamma_threshold` is assigned twice

Lines 64 and 329 of `tradelib_global_constants.py`, both `90` today. At import
the second wins. `write_config`'s multiline regex rewrites **both**, which is
what keeps them in step — accidentally rather than by design. Fix by deleting
one; nothing in this project needs to change.

### Unwinding before the trade opens

`unwind_trading_days_before` greater than `(expiry_weekday - selling_weekday) % 5`
closes the book before the position is opened. The run completes and takes zero
trades — a clean exit-0 producing a meaningless comparison. The tree enforces the
legal set as a leaf `domain`, so the dashboard's job count is already
post-constraint. A pre-constraint run confirmed it: Tuesday with offset 1 on a
Tuesday-expiry strategy returned exactly `0`.

### Bare `except` blocks

Several selection helpers wrap their whole body in a bare `except` — a
`TypeError` from a bad value is indistinguishable from "no strike found", and
`generate_trades` returns `[]` rather than raising. This is the structural reason
"the backtest succeeded" is not evidence that a parameter did anything.

## Validating an axis before trusting it

`docs/Axis_validation_plan.md` is the procedure. For each parameter an axis
sweeps, prove three things:

| Check | Method | Pass condition |
|---|---|---|
| **Reaches** | read the run's config snapshot | the parameter holds the planned value |
| **Moves** | two variants differing only in this parameter | `final_pnl` differs. Identical P&L means inert — gated off, or outside the range where it does anything |
| **Fails loudly** | one value deliberately out of range | raises, or at minimum logs something findable. Where it silently no-ops, record the safe range and constrain the leaf's `domain` |

**Moves** is the one that catches the gamma case, and the one most worth running
first on every parameter. Verify the backtest output itself before any of this —
until a run's own output is trustworthy, nothing an axis test says is meaningful.

## Working inside the backtest repository

Two standing constraints from the owner:

- **The repository carries their own uncommitted work.** Never `git checkout`,
  `git stash` or otherwise revert it. Undo a change by editing the specific lines
  back, or by copying from a snapshot you took.
- **Never commit or push there without asking**, and flag any engine-side change
  prominently **before** a sweep runs — not after.

Changes to the engine that this project has wanted are documented rather than
made: `docs/Weekday_first_hedging.md` describes the restructure that is blocked
on `underlying_threshold_hedge_type` becoming weekday-keyed (the constant already
is, at `hedge_component.py`'s `isinstance(cfg_constant, dict)` branch; the type
is still a plain scalar). When engine work is needed, write a self-contained
handoff document and let the owner implement it in their own process.
