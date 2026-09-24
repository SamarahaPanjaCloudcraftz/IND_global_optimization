# Backtest Engine Constraints

2026-09-21 · @Samaraha

How the optimization engine drives the backtest engine, the constraints that
follow from it, and two defects to fix on the backtest side.

## How configuration reaches the engine

The backtest engine has no configuration API. Its settings are module-level
globals in `tradelib/tradelib_global_constants.py`, imported by
`main_backtest_single_process.py` and read from there by everything downstream.

The only way to change what a run does is therefore to **edit the text of that
file** and then launch the pipeline. `tradelib_runner.write_config` rewrites one
assignment line per parameter, then runs the four scripts in order.

Everything below is a consequence of that one fact.

## 1. The constants file is shared mutable state

There is one copy of the file, and a run is set up by mutating it in place.

**Runs must be sequential.** Two backtests started concurrently would each
rewrite the same lines, and both would read whichever write landed last. The
sweep runner is sequential for this reason, not for simplicity. Parallelism is
not a tuning knob that can be turned on later — it is blocked by the design of
the configuration mechanism, and any attempt at it silently produces runs whose
recorded config does not match what actually executed.

**The last job's config is left behind.** When a sweep finishes, the file holds
whatever the final job wrote. A manual backtest run afterwards silently inherits
it. The hand-written `trade_simulator_nifty.py` driver has always behaved this
way, so this is not a regression — but it is a standing trap, and it gets worse
as sweeps get larger and less memorable.

**Why this is tolerable for now.** Every run writes its own config snapshot into
its output directory, so the record of what a given run actually used is intact
even though the shared file has moved on. The determinism of the directory hash
depends on the config, not on the file.

**What a fix looks like.** The engine accepts a config — a path to a per-run
settings file, or a dict passed to `BacktestDriver` — instead of importing
globals. That single change removes the sequential constraint, the residue
problem, and the need for `tradelib_format` to exist at all, since values would
no longer have to survive a round trip through Python source text.

## 2. `gamma_threshold` is assigned twice

It is assigned at **line 58** and again at **line 299** of
`tradelib_global_constants.py`. No other module-level variable in the file is
duplicated, and no top-level parameter is shadowed by a nested assignment.

Both assignments are currently `90`, so the file is consistent today and
`write_config` rewrites both to the same value. The problem is latent:

- At import, the second assignment wins. If the two ever diverge, line 58 is
  dead and misleading to read.
- The rewrite is a `^name\s*=.*$` match with `re.MULTILINE`, so it replaces
  every module-level assignment of that name. That is what keeps the two in
  step today, and it is accidental rather than intended.

**Fix:** delete one of them. Nothing in the optimization engine needs to change.

## 3. `gamma_hege_otm_outstrike` is a delta, not a strike distance

Two separate problems share this name.

**The spelling.** It is `hege`, not `hedge`, at line 79. Consistent across all
five files that reference it, with no correctly-spelled variant anywhere, so
nothing is broken - but the tree must spell it the same way or `write_config`
will refuse it. A rename is a mechanical find-and-replace.

**The meaning, which matters more.** In
`strategies/strategy_components/gamma_hedge_component.py`, the
`get_static_otm_option` path that would treat this as a strike distance is
commented out (lines 269-277). The live path passes it to
`get_delta_based_otm_option`, whose own parameter is named `delta_outstrike`
and which computes `delta_outstrike / 100`. So the value is **a delta in
percent**: the baseline `50` means delta 0.50, an at-the-money option, not 50
points out.

Swept as if it were a strike distance - `50, 100, 150` - the last two become
deltas of 1.0 and 1.5, which match no option. `get_delta_based_otm_option`
returns `None`, and the component logs critical and returns early:

```python
self.logger.critical(f"OTM PE not found, and strict condor is true. skipping {self.name}")
return trade_list
```

**The backtest does not fail.** It completes, writes a full results directory
and a plausible P&L, and records a config saying
`gamma_hege_otm_outstrike=150` - with nothing to indicate the gamma hedge never
happened. Selection would then compare that run against real ones.

Sweep values belong in 0-100 delta terms, the same units as `delta_outstrike`
for the condor. This is the case that motivates
`docs/Axis_validation_plan.md`.

**Fix:** rename to `gamma_hedge_delta_outstrike`, which would describe what it
actually does. Failing that, the domain has to be constrained in the tree.

## Related

- `tradelib_format.py` — turning native config values into source text, and why
  `repr()` is not sufficient for `date`/`time`.
- `tradelib_runner.py` — the rewrite-and-launch sequence.
- `config.toml` — where the engine lives and what interpreter runs it.
- `docs/Axis_validation_plan.md` — proving an axis actually does something
  before trusting it in a run.
