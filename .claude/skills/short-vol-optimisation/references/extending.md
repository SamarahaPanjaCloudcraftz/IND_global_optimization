# Changing and extending the engine

## The two standing rules

**No speculative features.** The owner has said this repeatedly: build what was
asked for, and if something else looks necessary, say so and let it be decided.
Unnecessary bloat is a real cost here — the whole `engine/` package is about 300
lines and its value is that you can hold it in your head.

**The layering is the design.** `tradelib_format.py` and `tradelib_runner.py`
are the only two files that know a backtest engine exists. The tree, the plan,
the selection and the dashboards never learn what a backtest is. A change that
makes `engine/` import `tradelib_runner`, or makes the analysis dashboard import
`strategies`, is going the wrong way — push the knowledge down into a recorded
artefact instead (that is what `groups` in `plan.json` is).

## Where changes belong

| Change | Where |
|---|---|
| a new parameter on an existing axis | the relevant method on `DowCondor`, plus the baseline |
| a new axis for both underlyings | a new method on `DowCondor` + add it to `tree` and to `groups` |
| a new axis for one underlying only | override that method in the subclass |
| different values for one underlying | the subclass — values only, never shape |
| a new strategy on a different shape | a new module in `strategies/` exposing `STRATEGY` |
| a new chart | a `@panel("Name")` function in `dashboards/analyse_run/panels.py` |
| a new UI control | `dashboards/plan_and_start/streamlit_app.py`, with the logic in `plan_io.py` |

`DowCondor` is deliberately all methods and properties, so an underlying that
needs a different axis overrides that one piece and inherits the rest — rather
than the choice being all-or-nothing between sharing a tree and forking it.

## Adding a parameter to an axis

1. **Confirm it exists in the constants file** as a module-level assignment. If
   it does not, `write_config` will raise at the first job — which is the correct
   behaviour, but find out now rather than then.
2. **Confirm what the engine does with it.** Read the component that consumes
   it. Is it weekday-keyed or a scalar? Is it gated behind another flag? What are
   its units? Getting this wrong is how a sweep produces a plausible P&L for a
   parameter that never took effect.
3. **Add the `Leaf`** to the right branch, with a `domain` if the legal set is
   fixed. Pin it with a single-value domain if the branch holds it constant.
4. **Add it to `baseline`** if the tree now sweeps it — otherwise the job that
   does not mention it inherits whatever the constants file was left holding.
5. **Add a default range** to the strategy's range attributes if it is a
   continuous quantity, so the dashboard prefills something sensible.
6. **Validate it** — reaches / moves / fails loudly (`backtest-engine.md`).

## Adding an axis

An axis is a child of the root sum. It needs:

- a method returning an `Internal(name, SUM, [...])` over its branches, each
  branch a `PRODUCT` of the parameters chosen jointly
- to be added to the `tree` property
- to be listed in `groups`, so the analysis dashboard can place it
- entries in `shared` for any parameter that should be set once and fanned out
- a `pending` entry instead, if the engine cannot support it yet (`IVWAP` is the
  example — no constant, module or reference exists for it)

Sum vs product is the decision that matters: **sum means "these can be optimised
separately and the winners composed"; product means "these interact and only
whole combinations are meaningful candidates."** Getting it wrong does not
crash — it produces a comparison that does not mean what it looks like.

Whether to branch by weekday: yes if the parameter's effect genuinely differs by
selling day. It does for anything expiry-relative (days to expiry differ by
weekday, so the same delta is a different instrument). Weekday branches use the
one-hot `day_of_week_signal_strength` so only that day sells.

## Adding a strategy

A module in `strategies/` exposing `STRATEGY`. If it is the same day-of-week
condor on another instrument, subclass `DowCondor` and supply values only — the
NIFTY and SENSEX modules are ~40 lines each and are the template. If it is a
genuinely different shape, write the tree directly; `discover()` only requires
the attributes listed in `tree-model.md`.

Widget keys in the planning dashboard are **scoped per strategy**. They have to
be: sharing them meant switching from NIFTY to SENSEX silently kept NIFTY's
values.

## Adding a chart panel

`panels.py` is a registry:

```python
@panel("Drawdown")
def drawdown(view): ...
```

`view` carries the axis, the selected frames, the colours and the theme. Adding
a panel does not touch `streamlit_app.py`. Series maths lives in `series.py`
(`pnl`, `drawdown`, `difference`, `summary`); colour choice lives in `charts.py`.

`charts.MAX_LINES = 8` and hues are **never cycled** — past eight, assign eight
and report how many are hidden. Cycling makes two different series the same
colour, which is worse than omitting one.

## Editing the dashboards

Both are meant to stay small and non-monolithic, because the requirements change
often:

- **plan_and_start** — the page is UI; anything testable belongs in `plan_io.py`,
  which imports no Streamlit.
- **analyse_run** — the page is a cascading drill-down and nothing else. It
  imports nothing from `engine`, `strategies` or `tradelib`, and performs no
  writes. Keep both properties true.

**A strategy edit needs a dashboard restart.** `discover()` goes through
`importlib.import_module` and so hits the `sys.modules` cache; Streamlit reruns
the page but never re-executes those modules. Until the server process is
restarted the dashboard shows the old counts and the old prefilled ranges, with
nothing to say so.

Three editing hazards, all of which have caused real bugs here:

- **Replace by anchored match, not by line slice.** A slice-based replacement
  once left two definitions of the same function in the file, and the stale one
  won. It was caught only because the signature had changed.
- **`[0, 1] == [False, True]` is `True` in Python.** Testing a value list for
  booleanness by equality rendered `unwind_trading_days_before` as an on/off
  toggle that would have written `True` into the config. Test
  `isinstance(value, bool)`.

## Known incomplete work

Before building on any of these, check they are still unfinished:

- **`select.compose` replaces dicts wholesale.** Per-weekday winners overwrite
  each other instead of merging per key. This has to be fixed before any composed
  config is trusted — it is the gate on automating stage-to-stage carry-forward.
- **Selection semantics are undecided** — whether a sum node is a choice between
  alternatives or a set of independent contributions. The `select` protocols are
  unwritten; winner picking is manual.
- **The entry-window constraint** — an entry end at or before the start sells
  nothing — is not enforced in the UI, the way the unwind constraint is.
- **Weekday-first delta hedging** is blocked on the engine. See
  `docs/Weekday_first_hedging.md`; nothing there is built.
- **Regression points A–F** were never re-run against the current branch
  defaults after the leg-selection and range changes.

## Verifying a refactor did not change behaviour

The technique that has worked here: snapshot the tree's observable surface
before and after, and diff.

```python
import pickle, sys; sys.path.insert(0, ".")
from strategies import discover
from engine import paths, branches, axes

def surface(strategy):
    tree = strategy.tree
    return {
        "leaves": {p: (l.values, l.domain) for p, l in paths(tree).items()},
        "branches": [b for a in axes(tree) for b, _ in branches(a)],
        "baseline": strategy.baseline,
        "shared": strategy.shared, "gated": strategy.gated,
        "varies": strategy.varies, "baselines": strategy.baselines,
        "ranges": strategy.ranges,
    }

pickle.dump({n: surface(s) for n, s in discover().items()},
            open("/tmp/before.pkl", "wb"))
```

Run it before the change, again after, and compare. Two refactors were proved
non-behaviour-changing this way — every leaf path with its values and domain
(235 per strategy as of 2026-09-24), the baseline, every branch, and every
declaration.

For the backtest side, the equivalent proof is byte-for-byte: run the same config
through the dashboard and by hand, and diff `blotter`, `portfolio`,
`consolidated_store`, `backtest`, `gamma_log` and `final_pnl`. That has been done
and matched exactly.
