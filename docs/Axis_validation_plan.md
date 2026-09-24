# Axis Validation Plan

2026-09-21 · @Samaraha

Before any axis is trusted in an optimization run, prove three things about each
parameter it sweeps: that it **reaches** the engine, that it **moves** the
result, and that it **fails loudly** when given a value outside its useful
range.

Verify the backtest output first — until a run's own output is trustworthy,
nothing an axis test tells you is meaningful.

## The failure this guards against

A swept parameter that quietly does nothing still produces a complete results
directory, a valid P&L, and a config snapshot naming the swept value. Nothing
crashes and nothing warns. Selection then compares that run against real ones
and may crown it the winner.

The known case is `gamma_hege_otm_outstrike`, which is a delta in percent rather
than a strike distance (see `Backtest_engine_constraints.md` §3). A sweep of
`50, 100, 150` matches no option for the last two values, and the gamma hedge is
silently skipped. Assume every parameter has a version of this until shown
otherwise.

## Part 1 — the backtest output

Do this once, before any axis work.

A run directory (written to `output_dir_folder`) contains:

```
backtest/  blotter/  consolidated_store/  gamma_log/  log/  portfolio/
result.json                      {"final_pnl": ...}   ← the score
tradelib_global_constants.py     the config snapshot for that run
```

| # | Check | How | Why it matters |
|---|---|---|---|
| 1.1 | Output lands where the plan said | Run one job, confirm the directory equals `job_dir(job, run, stage)` | If the engine writes elsewhere, every run overwrites the last |
| 1.2 | The snapshot matches the plan | Diff the run's `tradelib_global_constants.py` against `plan.json`'s config for that job | This is the only proof the config that ran is the config we asked for |
| 1.3 | `result.json` exists and holds `final_pnl` | Read it | It is the score `select` will consume |
| 1.4 | The same config twice gives the same `final_pnl` | Run one job, then re-run it | Without this, no comparison between variants means anything |
| 1.5 | A failing job is visible | Force a failure; confirm non-zero exit and `run.log` content | The sweep continues past failures by design, so they must be findable afterwards |

Note: the snapshot in 1.2 is copied automatically by
`backtest_combiner.py` (`shutil.copy`, line 172) — it is not something the
optimization engine arranges, so it also stops being true if that line changes.

## Part 2 — the three checks, per parameter

| Check | Method | Pass condition |
|---|---|---|
| **Reaches** | Read the run's config snapshot | The parameter holds the planned value |
| **Moves** | Two variants differing only in this parameter | `final_pnl` differs. Identical P&L means inert — gated off, or outside the range where it does anything |
| **Fails loudly** | One value deliberately out of range | Raises, or at minimum logs something findable. Where it silently no-ops, record the safe range and constrain the leaf's `domain` |

"Moves" is the check that catches the gamma case, and it is the one most worth
running first on every parameter.

## Part 3 — per-axis checklist

Constant names below are exact and verified against
`tradelib_global_constants.py`. Anything marked **unresolved** needs an answer
before that part of the axis can be built.

### Hedge optimization

#### gamma_hedging

**Gate:** `gamma_hedge` is `False` in the baseline, and the whole component sits
behind `if gamma_hedge:` (`day_of_week_static_condor_strategy.py:36`). Nothing
in this axis does anything until it is `True`, so it belongs in the axis's
product rather than sitting at baseline.

| Table name | Constant | Baseline | Notes |
|---|---|---|---|
| threshold | `gamma_threshold` | 90 | Also assigned at line 299; both get rewritten |
| pct_hedge | `percent_hedge` | 0.5 | |
| otm_outstrike | `gamma_hege_otm_outstrike` | 50 | **Delta × 100, not strike distance.** Silently skips the hedge out of range |
| trade direction | `gamma_hedge_trade_direction` | `"both"` | Closed domain `both / buy / sell`; raises on anything else (verified) — the one parameter here that already fails loudly |

#### delta_hedging

**Gate:** `underlying_threshold_hedge_flag`, `True` in the baseline.

| Table name | Constant | Baseline | Notes |
|---|---|---|---|
| hedging mode | `underlying_threshold_hedge_type` | `"gamma_iv"` | Closed set — enumerate the accepted values before building the domain |
| hedge_constant (per weekday) | `underlying_threshold_hedge_constant` | `{0: 2100, 1: 2800, 2: 500, 3: 50, 4: 188}` | Index points, so NIFTY and SENSEX values are not interchangeable |
| pct hedging | **unresolved** | — | `percent_hedge` belongs to gamma hedging. Candidates: `custom_pct_to_hedge`, `pct_to_spend` |

### Sell optimization

#### condor_OTM_outstrike

Four mutually exclusive structures, each a different `strategy_to_execute` with
its own parameters. Selected as one winner, never composed.

| Mode | `strategy_to_execute` | Its parameters |
|---|---|---|
| static spot based wings | `DAY_OF_WEEK_STATIC_CONDOR_STRATEGY` | `OTM_outstrike`, `steps` |
| delta based wings | `DAY_OF_WEEK_DELTA_CONDOR_STRATEGY` | `delta_otm`, `delta_outstrike` |
| static spot based strangle | `DAY_OF_WEEK_STATIC_STRANGLE_CONDOR_STRATEGY` | `strangle_outstrike` |
| delta based strangle | **unresolved** | No day-of-week variant exists; `DELTA_STRANGLE_STRATEGY` is not one |

Extra check for this axis: confirm each mode's parameters are ignored by the
other modes. If a stale `delta_outstrike` changes a static-wings run, the modes
are not as separable as the table implies.

#### trade_time

| Table name | Constant | Baseline |
|---|---|---|
| start | `trade_start_time` | `time(9, 17, 0)` |
| stop | `trade_end_time` | `time(15, 30, 0)` |

Check that a start after the stop fails rather than producing an empty run that
still writes a P&L of zero.

#### dow_signal_strength

| Table name | Constant | Baseline | Notes |
|---|---|---|---|
| sell weekday | `day_of_week_signal_strength` | `{0: 3, 1: 2, 2: 0, 3: 0, 4: 0}` | "Sell only on one weekday per run" means one-hot dicts, five variants |
| unwind day | **unresolved** | — | No per-weekday unwind constant found; `unwind_time` and `unwind_time_before` are global |
| unwind time | `unwind_time` | `time(14, 50, 0)` | |

#### IVWAP

**Not implemented.** No constant, module or reference anywhere in the backtest
repo. Can be declared as a planned axis, but there is nothing to sweep until it
exists.

## Part 4 — record

Fill in as each is checked. An axis is not usable in a run until every one of its
parameters has three passes.

| Axis | Parameter | Reaches | Moves | Fails loudly | Safe range | Checked | Notes |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

## Open questions

1. `delta based strangle` — is a day-of-week variant planned, or is that row dropped?
2. Delta hedging's percentage parameter — which constant?
3. Unwind day — does a per-weekday unwind parameter exist, or is it to be added?
4. IVWAP — what does it control, and what parameters would it sweep?
5. Accepted values for `underlying_threshold_hedge_type`, to fix its domain.
