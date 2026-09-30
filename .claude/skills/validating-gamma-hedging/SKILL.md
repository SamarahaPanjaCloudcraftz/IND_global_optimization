---
name: validating-gamma-hedging
description: Verify that a gamma_hedging axis run (or any gamma-hedge-bearing config) is actually doing what its config claims — weekday isolation, condor structure, threshold triggering, percent_hedge scaling — using only the job's own output directory (blotter, consolidated_store, tradelib_global_constants.py, result.json). Use this whenever a gamma_hedging sweep has just run or is running and needs sanity-checking before its results are trusted, or when debugging why a gamma-hedged run's P&L looks implausible. This is the playbook that caught the gamma_threshold sign bug on 2026-09-24/25.
---

# Validating a gamma_hedging run

This is the procedure that was actually run, end to end, on the NIFTY
`gamma_hedging` axis of the `global_optimisation_engine` project (see the
`short-vol-optimisation` skill for the project itself). It found a real bug —
`gamma_threshold` swept over negative values, silently defeating the trigger —
and everything below is what caught it. Reuse this checklist for any future
gamma_hedging run, and adapt the same *shape* of check (config reaches → trades
happen → the swept parameter measurably changes behavior) for other axes.

Everything here needs only files already sitting in a completed job's output
directory — no code changes, no re-running:

```
<job>/tradelib_global_constants.py   the exact config that ran (the snapshot)
<job>/result.json                    {"final_pnl": ...}
<job>/blotter/blotter_YYYYMMDD*.csv  every trade, tagged by trade_component
<job>/consolidated_store/YYYYMMDD.csv  per-minute portfolio_gamma, portfolio_delta, spot, ...
```

## 1. Config reaches: diff against control

Diff a completed job's `tradelib_global_constants.py` against the control job's
(same run, `axis=None`). Everything that differs should be explainable:

```bash
diff <(grep -v "^output_dir_folder" control/<hash>/tradelib_global_constants.py) \
     <(grep -v "^output_dir_folder" gamma_hedging/<weekday>/<hash>/tradelib_global_constants.py)
```

Expect exactly: `gamma_hedge` flips `False→True`, the swept `gamma_threshold` (and
`percent_hedge` if that's the other swept param), and the one-hot
`day_of_week_signal_strength` for the branch's weekday. Nothing else. If more
lines differ, something is leaking between branches or baseline isn't pinned
correctly.

Note `gamma_threshold` is assigned **twice** in
`tradelib_global_constants.py` (lines ~64 and ~318 as of 2026-09-24) —
`write_config`'s multiline regex rewrites both, so check both stay in sync in
the snapshot. If they diverge, the write didn't actually apply everywhere it
needed to.

## 2. Weekday isolation: tally trade_component by day

Every gamma_hedging branch is one-hot on a single weekday. Tally
`trade_component` per calendar day across every `blotter_*.csv` in the job:

```python
import csv, glob
from datetime import date
for f in sorted(glob.glob(f"{job_dir}/blotter/*.csv")):
    datestr = f.split("/")[-1].split("_")[1][:8]
    d = date(int(datestr[:4]), int(datestr[4:6]), int(datestr[6:8]))
    counts = {}
    for row in csv.DictReader(open(f)):
        c = row["trade_component"]
        counts[c] = counts.get(c, 0) + 1
    if counts:
        print(d, d.strftime("%A"), counts)
```

Expect, for a NIFTY Monday branch (NIFTY expiry weekday = Tuesday, so
`unwind_trading_days_before=0` on the Monday branch unwinds next-day):

- **Monday**: `day_of_week_static_condor_component` (many rows — this strategy
  is a gradual position build-up, re-entering every `trade_interval` minutes
  through the whole entry window, not a single trade) + hedge activity
- **Tuesday**: hedge activity + `unwind_component`
- **Wed/Thu/Fri**: empty

A branch selling on a day further from expiry (e.g. Wednesday, with legal
unwind offsets `[0..4]`) instead holds the position open across the whole week,
entering only on its own day and unwinding on the *next* week's expiry day —
confirm whichever pattern the branch's `unwind_trading_days_before` and expiry
weekday actually imply, don't assume same-week unwind.

Any `gamma_hedge_component` or `day_of_week_static_condor_component` row on a
day it shouldn't be on is a real bug, not a data quirk.

## 3. Condor structure and instrument sanity

Grep a Monday (or any entry-day) blotter's `day_of_week_static_condor_component`
rows: expect four legs per tranche — two short (ATM CE + ATM PE, negative
position) and two long (further OTM CE + OTM PE, positive position, same
magnitude) — the classic iron condor. `dow_strangle_leg` /`dow_wing_leg` in the
config snapshot should read `{'type': 'atm', ...}` / `{'type': 'static', ...}`
for the baseline shape (a different `condor_OTM_outstrike` axis varies these;
gamma_hedging should always be running the baseline leg selection).

The gamma hedge trades themselves (`gamma_hedge_component` rows) should always
come in matched CE/PE pairs, same strike, same size, opposite... no — **same
sign**, both positive when buying, both negative when selling (this is a long or
short *straddle*, not a strangle): it's buying/selling gamma via an ATM
straddle, not delta-hedging via a synthetic future (that's `hedge_component`,
which trades CE and PE with *opposite* signs to replicate a future). Don't
confuse the two components' blotter rows.

```bash
grep "gamma_hedge_component" blotter/*.csv | awk -F',' '{print $8}' | sort | uniq -c
```

If `gamma_hedge_trade_direction = 'both'`, expect to see both signs somewhere in
the job (buys dominate; sells happen when trimming an existing hedge as the
underlying moves back). If it's pinned to `'buy'` or `'sell'`, expect only that
sign, ever.

## 4. Does percent_hedge actually move the trade size?

`percent_hedge` multiplies the gamma hedge ratio unconditionally
(`gamma_hedge_component.py`): `hedge_ratio = (portfolio_gamma/future_multiplier
/ gamma_of_strangle) * percent_hedge`. It's read differently on the delta side —
see `references/backtest-engine.md` in `short-vol-optimisation` for the
asymmetry — but on the gamma side it's always live.

Find two jobs in the **same weekday branch with the same `gamma_threshold`**,
differing only in `percent_hedge`. Compare their **very first** gamma hedge
trade of the day (before the two runs have had a chance to diverge — after the
first hedge, subsequent portfolio state differs between the two jobs and later
trades won't show a clean ratio):

```bash
grep "gamma_hedge_component" blotter/blotter_<first-day>*.csv | head -2
```

The lot sizes should scale by (within lot-size flooring) the ratio of the two
`percent_hedge` values. This was confirmed empirically: `percent_hedge=0.25` →
62 lots, `percent_hedge=0.8125` → 202 lots, ratio 3.25 ≈ 0.8125/0.25 = 3.25,
`62 × 3.25 = 201.5 ≈ 202`.

## 5. Does the threshold actually gate anything? (the check that found the bug)

This is the one that matters most and is easiest to get wrong by only checking
"trades exist." A threshold that never blocks anything still produces a
plausible-looking run with real trades and real P&L — it just isn't measuring
what its own config claims to sweep.

Pull `portfolio_gamma` per minute straight from `consolidated_store/<date>.csv`
(column present directly, no need to parse logs) and cross-reference against
the trade timestamps in the blotter:

```python
import csv
rows = list(csv.DictReader(open(f"{job_dir}/consolidated_store/<date>.csv")))
# rows before a trade have trade_done == "False"; that's the value the
# component's own gate saw when it decided whether to fire.
above = [r for r in rows if r["trade_done"] == "False"
         and abs(float(r["portfolio_gamma"])) >= threshold]
print(len(above), "ticks crossed the threshold")
for r in above:
    print(r["timestamp"], r["portfolio_gamma"])
```

Then confirm every `gamma_hedge_component` blotter timestamp lines up with one
of those ticks, and that ticks below threshold produced no trade. On a
correctly-signed positive threshold this lined up exactly: 2 ticks all day
crossed `|gamma| >= 40` (at -40.27 and -41.32), and those were the only two
timestamps with a gamma hedge trade; gamma dropped back under 40 immediately
after each one.

**The bug this caught:** the trigger is
`abs(portfolio_greeks.gamma)/future_multiplier >= self.gamma_threshold`. `abs()`
is always ≥ 0. If `gamma_threshold` is negative — which the axis's default
sweep ranges were, in both
`strategies/nifty_short_vol_dow_condor.py` and
`strategies/sensex_short_vol_dow_condor.py`
(`gamma_threshold_ranges`, e.g. Monday was `(-40.0, -80.0, 5)`) — the
inequality is trivially true for *any* portfolio gamma, including near-zero.
The gate never blocks. This wasn't a `future_multiplier` scaling issue
(`future_multiplier = 1` throughout, no scaling involved) — it was purely the
sign of the swept value versus a magnitude comparison the code had already
taken `abs()` of. The control's own baseline value, `gamma_threshold = 90`
(NIFTY) / `0.04` (SENSEX), was positive and worked correctly the whole time;
only the axis's *sweep range* had the wrong sign. Confirmed independently before
finding the root cause: gamma-hedge trade *count per day* was flat across every
`gamma_threshold` value tested at fixed `percent_hedge`, and only tracked
`percent_hedge` — a threshold that's supposed to gate frequency but shows zero
correlation with frequency is the signature to watch for on any axis.

**Fix**: flip the sign of `gamma_threshold_ranges` in both strategy files to
match the baseline's sign (keep the same magnitudes). After the fix, re-running
step 5 above should show the threshold discriminating: trades only at ticks
where `|gamma|` actually crosses the swept value, and gamma visibly dropping
back under threshold immediately after each hedge fires.

## 6. Does the swept threshold actually change frequency/PnL across its own range?

Once step 5 confirms the gate discriminates *within* one job, confirm it also
discriminates *across* the swept range: same weekday, same `percent_hedge`, only
`gamma_threshold` differing. Expect trade *count* to fall as the threshold rises
(a bigger gamma move is required to trigger), and `final_pnl` to differ
meaningfully, not be flat. (This is the same "moves" check
`docs/Axis_validation_plan.md` in `short-vol-optimisation` describes generically
— this file is what that check looks like in concrete gamma_hedging terms.)

## What this does not check

- Whether the *magnitude* of the corrected ranges (e.g. Monday 40–80) is a
  sensible business choice — only that the mechanism now responds to whatever
  range is given.
- The `gamma` and `gamma_iv` delta-hedging modes' own threshold formulas
  (`_compute_spot_move_threshold` in `hedge_component.py`), which divide by
  `abs(raw_gamma)` rather than compare directly — worth running an equivalent
  sign/discrimination check on those before trusting a `delta_hedging` sweep
  in `gamma` or `gamma_iv` mode.
- SENSEX — the same sign bug existed in its `gamma_threshold_ranges` and was
  fixed at the same time, but has not yet had its own sweep run through this
  checklist.
