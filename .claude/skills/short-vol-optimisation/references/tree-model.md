# The model: trees, axes, branches, jobs

Everything in `engine/` follows from one shape: **the parameter space is a sum
of products**. Read `docs/Optimization_tree.md` for the original design; this
file is what the code actually does today.

## Contents

- [Nodes](#nodes)
- [Domains: open, closed, fixed](#domains-open-closed-fixed)
- [Path addressing](#path-addressing)
- [Branches — the terminal group](#branches--the-terminal-group)
- [From tree to jobs](#from-tree-to-jobs)
- [Deterministic hashing and the output layout](#deterministic-hashing-and-the-output-layout)
- [What a strategy object exposes](#what-a-strategy-object-exposes)
- [The five axes today](#the-five-axes-today)
- [Sweep ranges](#sweep-ranges)

## Nodes

Two concrete kinds, in `engine/tree.py`:

```python
Leaf(name, values, domain=None)          # a parameter and the values to test
Internal(name, combiner, children)       # combiner is SUM or PRODUCT
```

`SUM` and `PRODUCT` are combiner *strategies* plugged into the same `Internal`
node — there is no third node type.

- **SUM** — independent axes. Counts add; partial-config lists concatenate.
  Optimising one child does not change another child's winner.
- **PRODUCT** — interacting parameters. Counts multiply; partials
  cross-multiply. Keys are disjoint across product siblings, so merges never
  collide. A product's winner is a **whole cell**, never one parameter at a time.

Independent axes sit on top, interacting grids underneath — never the reverse.

## Domains: open, closed, fixed

A leaf's `domain` is its legal set, and it decides how the planning dashboard
renders it:

| `domain` | Meaning | UI |
|---|---|---|
| `None` | open parameter — no fixed set could cover what you might want | multiselect that accepts new values, or a min/max/count range |
| a list | closed — the strategy fixes what exists (booleans, mode identifiers) | pills, choose from the list |
| a single-value list | pinned — the branch *is* this value | caption; unsweepable |

`Leaf.__init__` raises if any value is outside a declared domain, so a bad tree
fails at construction rather than at write time.

The single-value domain is load-bearing. It is how a branch says "this is what I
hold constant so that the thing I am named after is the only thing varying" —
e.g. `condor_OTM_outstrike/delta wings` pins `dow_strangle_leg` and frees
`dow_wing_leg`.

## Path addressing

Once the tree has branches, the same parameter name appears in many places —
fifteen leaves are called `underlying_threshold_hedge_constant`. So a leaf is
addressed by **where it sits**, not by what it is called:

```
delta_hedging/gamma/Monday/underlying_threshold_hedge_constant
condor_OTM_outstrike/delta wings/Wednesday/dow_wing_leg
```

`paths(node)` returns `{path: Leaf}` relative to that node. `sweep(node, chosen)`
takes the user's values keyed by the same paths and returns a copy of the tree
with them substituted. It raises on a path the tree does not declare, and the
`Leaf` constructor raises on a value outside a domain — so the user cannot
introduce a parameter or an illegal value, only choose within the universe.

## Branches — the terminal group

```python
branches(node) -> list[tuple[str, Node]]
```

Walk down through **sum** nodes; stop at the first non-sum. Each stop is a
terminal group of runs: one grid, generated whole.

This is the unit of three separate things, and they agree by construction:

- **tagging** — `Job.axis` is the full branch path, e.g.
  `delta_hedging/gamma_iv/Monday`
- **directory layout** — each path segment becomes a directory
- **charting** — the analysis dashboard drills down group → axis → weekday and
  shows charts at exactly this level

A sum is walked through because its children are separate groups stored and
tagged apart. A product is terminal because its parameters are chosen jointly,
and a nested path would invite reading one level independently of the other.

## From tree to jobs

`engine/plan.py`, four steps — two inside the nodes, two outside them so the
nodes stay pure structure:

1. **generate** — each subtree emits *partial* configs naming only the
   parameters it touches.
2. **overlay** — complete each partial on the baseline:
   `{**baseline, **partial}`. This is the only place the baseline enters and the
   only place unmentioned parameters get filled.
3. **dedup** — every axis includes its baseline value, so the all-baseline
   config is emitted once per axis. Dedup collapses them into the single
   standalone **control** job (`axis=None`).
4. **tag** — each remaining job carries its branch path.

`plan_size(root, baseline, only)` reports `variants` (what the tree emits before
dedup), `runs` (what will actually execute) and `deduped`. That is what drives
the dashboard's job-count and time estimate.

`build_jobs(root, baseline, only=None)` — `only` restricts to a subset of axes
by name. To stage a one-way dependency x → y: run x's axes, compose the winners,
and pass that config in as the baseline for y's axes.

Dedup needs configs to be comparable, which is what `freeze` / `canonical` are
for. `freeze` sorts dict keys (so the same mapping written in a different order
is the same config) and tags the sequence type (so `[1, 2]` and `(1, 2)` stay
different — they produce different source text).

## Deterministic hashing and the output layout

```
<run>/<stage>/<axis>/<branch…>/<config hash>/
```

`config_hash` is a sha256 over `repr(canonical(config))`, truncated to 12 chars.
Python's builtin `hash()` is unusable here — string hashing is randomised per
process unless `PYTHONHASHSEED` is set, so directory names would differ between
runs of the same plan.

The digest is of the **complete** config, not of the partial that defines the
variant. So the same config lands in the same directory name in any axis, any
stage, any run — which is what lets results be joined across them later.

Whitespace in a branch name becomes an underscore (`static wings` →
`static_wings`).

## What a strategy object exposes

`strategies/__init__.py::discover()` imports every module in the package and
collects its `STRATEGY` instance. Each one carries:

| Attribute | What it is |
|---|---|
| `name` | how it appears in the dashboard |
| `runner` | `"module:attribute"` of the `run_one(job, out_dir)` that runs a backtest — `"tradelib_runner:run_one"` today |
| `tree` | the `Node` defining the universe |
| `baseline` | a complete config, one value per parameter |
| `groups` | axis → which dashboard group it belongs to; recorded in `plan.json` so analysis needs only the run directory |
| `shared` | parameters offered once and fanned out across an axis's branches (optionally scoped to a sub-path) |
| `varies` | dict parameters whose branch varies one named key rather than a weekday (`dow_strangle_leg` → `"value"`) |
| `baselines` | dict parameters whose non-swept keys actually matter, so the branch needs a settable baseline |
| `gated` | a parameter the engine ignores unless another is on — `percent_hedge` behind `custom_pct_to_hedge` |
| `pending` | axes named in the optimisation table that cannot be built yet (`IVWAP`) |
| `ranges` | default min/max/count per leaf path, prefix, or bare name |

**A gate applies only where its gating parameter exists in that node's paths.**
This mattered: `percent_hedge` was once silently pinned and hidden on the gamma
axis, where `custom_pct_to_hedge` does not exist, which would have run five
identical jobs labelled as a sweep.

### The class hierarchy

`DowCondor` (in `strategies/dow_condor.py`) holds the **entire shape** — every
piece is an overridable method or property. `Nifty` and `Sensex` are ~40 lines
each and supply **values only**. Two copies of one structure drift and nothing
notices; this way a structural change cannot land on one underlying and miss the
other, while an underlying that genuinely needs a different axis overrides that
one piece.

Anything derivable is derived: `expiry_weekday` gives `expiry_info` and the legal
unwind offsets, the session times give the default entry windows, and
`otm_outstrike` gives the static wing percentage.

What differs between the two:

| | NIFTY | SENSEX |
|---|---|---|
| expiry weekday | 1 (Tuesday) | 3 (Thursday) |
| lot size | 65 | 20 |
| steps | 50 | 100 |
| OTM outstrike | 5 | 4 |
| session | 09:17–15:30 | 09:20–15:29 |
| baseline `percent_hedge` | 0.5 | 1.0 |
| `gamma_threshold` | 90 | 0.04 |

Both share the same delta-hedge baseline: **static mode, 16 basis points, every
weekday**. Every axis holds that while varying its own parameters, so every
result is a departure from the same thing.

## The five axes today

Counts below are the tree's declared variants before the user narrows them.
Identical for both strategies.

| Axis | Variants | Branches | What it measures |
|---|---|---|---|
| `gamma_hedging` | 5 | 5 (weekdays) | gamma hedge threshold and pct, per selling weekday. `gamma_hedge` is pinned `True` — choosing the axis *is* the decision to hedge; the control stays unhedged |
| `delta_hedging` | 15 | 15 (3 modes × weekday) | `static` / `gamma` / `gamma_iv` threshold constants against the hedge pct. Mode is a sum, not a product with k, because the k ranges are on entirely different scales per mode |
| `condor_OTM_outstrike` | 30 | 30 (6 methods × weekday) | six strike-selection methods: static wings, delta wings, static short, delta short, and the last two with no wings. Each varies **one** leg group and pins the other |
| `trade_time` | 5 | 5 (weekdays) | `day_of_week_entry_start_time` × `day_of_week_entry_end_time` per weekday, inside the session bounds |
| `dow_signal_strength` | 15 | 5 (weekdays) | `unwind_time` × `unwind_trading_days_before` per selling weekday |

**Every weekday branch works by a one-hot signal.** `day_of_week_signal_strength
= {0:1, 1:0, 2:0, 3:0, 4:0}` means only Monday sells, which is what makes a
Monday branch a measurement of Monday alone. It is pinned with a single-value
domain in every weekday branch.

**Weekday branching is not decoration.** Days to expiry differ by weekday — a
Monday sale is one day out, a Wednesday four — so the same delta or percentage
is a different instrument on each, and the best choice need not agree.

### The unwind constraint

Legal offsets for a selling weekday:

```python
list(range((expiry_weekday - weekday) % 5 + 1))
```

A position sold on `weekday` reaches expiry after that many trading days.
Unwinding earlier closes the book **before the trade is opened** — the run
completes normally and takes zero trades. Equal to the gap is legal: it unwinds
on the selling day.

For NIFTY (Tuesday expiry): Mon `[0,1]`, Tue `[0]`, Wed `[0,1,2,3,4]`,
Thu `[0,1,2,3]`, Fri `[0,1,2]` — 15 legal combinations. The domain enforces it,
so the job count shown in the dashboard is already post-constraint.

This is the nominal weekly cycle, so a holiday-shifted expiry can leave a
ceiling value idle for that week.

## Sweep ranges

Sweep values are entered as **min / max / how many** and expanded to equally
spaced values by `plan_io.spread`. Times interpolate through seconds-of-day and
round to the minute; integer endpoints stay integers, so a count of strikes
never becomes 4.666.

Defaults come from the `ranges` sheet of `ind short vol.xlsx` and live on the
strategy classes. `strategy.ranges` emits them keyed by **leaf path, prefix, or
bare parameter name**, most specific winning — so a range that is the same
everywhere is stated once, and one that differs per weekday is stated per
weekday. That is how `percent_hedge` keeps a different default on the gamma axis
(`gamma_hedging/percent_hedge`) than on the delta side, while writing to the same
config key.

A shared control (one setting fanned out to every branch) defaults to the
**union of what every branch declares**, not to the first branch's values — the
branches have different legal sets and seeding from one would clamp the others
down to it.
