# Weekday-first delta hedging — deferred

2026-09-23 · @Samaraha

A restructure we want but cannot do yet, the engine change it waits on, and what
to do when that lands. Nothing here is built.

## What we have

`delta_hedging` nests **mode above weekday**:

```
delta_hedging
├── static      ├── Monday … Friday     k range × pct
├── gamma       ├── Monday … Friday
└── gamma_iv    └── Monday … Friday     = 15 branches
```

Branch tags and output directories read `delta_hedging/static/Monday/<hash>`.

## What we want

**Weekday above mode**, so everything groups by selling day:

```
delta_hedging
└── Monday        signal = 10000, unwind offset = 0
    ├── static      k range × pct
    ├── gamma       k range × pct
    └── gamma_iv    k range × pct
└── Tuesday …
```

Directories would read `delta_hedging/Monday/static/<hash>`, matching
`dow_signal_strength/Monday/<hash>`. Both axes would then answer "what did
Monday do" the same way, and cross-axis comparison lines up without translation.

The job set is **identical** either way. This is purely about grouping.

## Why we cannot do it yet

The engine treats the two parameters differently.

**The hedge constant is weekday-aware** —
`strategy_components/hedge_component.py:179-181`:

```python
cfg_constant = self.underlying_threshold_hedge_constant
if isinstance(cfg_constant, dict):
    cfg_constant = cfg_constant[timestamp.weekday()]
```

**The mode is a plain scalar** — `hedge_component.py:184` passes
`self.underlying_threshold_hedge_type` straight into
`_compute_spot_move_threshold`, which branches on it as a string
(`hedge_component.py:36-50`: `"static"`, `"gamma"`, `"gamma_iv"`, else raise).
There is no dict branch and no weekday lookup.

So **one run has exactly one hedging mode**, and a weekday-first tree would be
claiming a freedom the engine does not have. Mode has to sit above weekday
because a mode is a property of the whole run, while the constant is a property
of each day within it.

There is a second reason, smaller but real: the **k baseline is a mode-level
concept**. `static` thresholds sit around 11 and `gamma_iv` around 2100, and the
baseline is what the other four weekdays hold while one is swept. With mode
above weekday it is entered once per mode. Invert the nesting and it has
nowhere natural to live.

## Precondition

`underlying_threshold_hedge_type` becomes weekday-keyed in the engine, the same
way the constant already is — accept a dict and index it by
`timestamp.weekday()` before the string comparison in
`_compute_spot_move_threshold`. Keeping the scalar form working means an
`isinstance(..., dict)` branch exactly like the constant's, so existing configs
are unaffected.

## When that lands

1. **Invert the tree** in both strategy files: `delta_hedging` becomes a sum over
   weekdays, each weekday a sum over modes, each mode a product of its k range
   and pct. Same leaves, same job set.
2. **Hoist the k baselines** into one axis-level block — 3 modes × 5 weekdays,
   entered once — rather than repeating them inside all five weekday blocks.
   This is the only part with a real design choice in it.
3. **Nothing else changes.** Path-addressed sweeps, the branch selector, the
   share toggles and the per-weekday domains are all indifferent to nesting
   order; they key off leaf paths, not depth.

## Not blocked by any of this

**Pinning `unwind_trading_days_before` to `0` inside each delta hedging branch.**
Today the axis does not mention it, so it inherits whatever the baseline holds —
which is fine while the baseline says `0`, but means a delta hedging config does
not state its own unwind. Pinning it makes each config self-describing and
independent of what a previous stage left behind. The per-weekday legal domain
built for `dow_signal_strength` applies unchanged.

This can be done at any time, under either nesting.

## Related

- `Backtest_engine_constraints.md` — what else the engine does and does not
  support per weekday.
- `Axis_validation_plan.md` — proving an axis does something before trusting it.
