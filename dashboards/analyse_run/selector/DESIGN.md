# Variant selector: design

Status: built and wired into the analysis dashboard. The selector is a **standalone module**: it
reads a table and a config, and returns a selection plus an audit.

**Wiring (2026-10-07).** `ranking.top(table, "Selector", n)` calls `select()` with `default.toml`
and `n` overridden, on exactly the ranked table the page shows (baseline row included). "Selector"
is one more choice in every Top-by / Best-by control (Summary, best of each group,
recommendation), and the default in each of them. When the selector picks nothing (D11, or a config
or data error) the page says why and picks nothing — it never falls back to another basis. The
detail view's Ranking table and the Combine page's candidate tables show each row's front,
score, rank, status and reason (`ranking.selector_audit`). The Combine page's n sets how many
picks each axis carries into the combinations.

Because it is standalone, it cannot rely on its callers having checked anything. Every input it
receives (the config and the data) is checked against an explicit list of errors (§5, §6) before
any selection happens. Anything ambiguous stops the run with an error. The selector never guesses.

## In short

1. **Check the config.** Every field is checked before any data is read (§5).
2. **Check the data.** Every metric must exist and be numeric, and IDs must be unique. A variant
   with a missing (NaN) or infinite value is thrown out, and the reason is recorded. If a metric's
   signs don't match the config, the run stops with an error (§6).
3. **Make every metric "bigger is better".** Flip the metrics where smaller is better, using the
   magnitude when there is a sign convention. After this step nothing needs to know about
   directions or signs.
4. **Apply the hard limits.** Throw out variants that break a limit. Limits are checked on the
   real, unscaled values. If nothing survives, stop and report how many variants each limit
   removed.
5. **Remove the clearly beaten (Pareto).** A variant is out if another variant is at least as good
   on every metric and better on at least one. The survivors form front 1. If front 1 is too
   small, front 2 is kept in reserve.
6. **Build the ideal point.** Take the best value of each metric among front 1: the imaginary
   perfect variant. Also take the worst value of each metric among front 1.
7. **Measure each variant's gap.** For each metric, compute how far the variant is from the best,
   as a fraction of the best-to-worst range: 0 = best, 1 = worst.
8. **Score.** The weighted average of the gaps. Lower is better.
9. **Pick the top n.** Take front 1 in score order, then front 2 if more are needed. Ties are
   broken by variant ID.
10. **Report everything.** For every variant: kept or not, why, its gaps, its score and its rank.

## 1. Purpose and scope

The selector picks the top `n` variants from a set of backtested variants, treating the choice as a
multi-objective optimisation problem. The objectives are metrics such as P&L, drawdown and Sortino.
The selector does not care what a metric means financially. It only needs each metric's direction
and sign convention, and it checks both.

It will be called many times: once per sweep axis, and again on the backtests of the combined
winners. Every call is driven by the same config. Each call is deterministic and keeps no state
between calls. **Scores are only comparable within one call**, because the ideal and nadir points
are recomputed every time. Only ranks inside a call mean anything.

Dependencies: numpy and pandas only. Tests use the standard-library `unittest`
(run from `analyse_run/`: `python -m unittest selector.test_selector -v`).

Files: `spec.py` (config, §4–5), `stages.py` (data checks, canonical form, constraints, §6–7),
`pareto.py` (dominance and fronts, §7), `pipeline.py` (scoring, selection, audit, §8–10),
`default.toml`, `test_selector.py`.

## 2. Interface

**Input**

- `table`: a pandas DataFrame with one row per variant. It holds one ID column plus one column per
  declared metric. Other columns are ignored.
- `config`: a TOML file path or an equivalent dict (§4).

**Output**

- `selected`: the selected variant IDs (as given in the table, not converted to strings), in rank order.
- `audit`: a DataFrame with one row per input variant (§10).
- `run`: run-level information (§10).

## 3. Why this order

Normalisation happens **after** constraints and Pareto sorting. Constraints need raw,
human-readable thresholds, and Pareto dominance doesn't change under rescaling, so neither needs
normalised values.

## 4. Config

### Default config

`selector/default.toml` uses three objectives, with column names matching `ranking.py`.
All `eps` are 0, and there are no groups and no constraints. Copy it and set `n` and the constraints
for each use:

```toml
[selection]
n         = 3          # how many variants to select
id_column = "Variant"  # column holding the variant IDs

[[metric]]
name      = "Final P&L"
direction = "max"

[[metric]]
name      = "Max drawdown"
direction = "min"
sign      = "auto"     # ranking.py stores it negative; auto checks this

[[metric]]
name      = "Sortino"
direction = "max"

# Constraints are optional. Example:
# [[constraint]]
# metric = "Max drawdown"
# op     = "<="
# value  = 250000      # |drawdown| <= ₹2.5L, however it's stored
```

Fields left out take their defaults (below): `role = "objective"`, `sign = "none"`, `eps = 0`.

### Adding a metric

Adding a metric takes two things, and no code change:

1. a `[[metric]]` block in the config (at least `name` and `direction`);
2. a column with that name in the input DataFrame.

Before adding one, check whether it measures the same thing as an existing metric. If it does, put
the two in the same `group` (§9).

### Fields

`[selection]`

| Field | Required | Default | Rule |
|---|---|---|---|
| `n` | yes | n/a | Integer ≥ 1 |
| `id_column` | no | `"Variant"` | Non-empty string. Must not also be a metric name. |

`[[metric]]`

| Field | Required | Default | Rule |
|---|---|---|---|
| `name` | yes | n/a | Non-empty string, unique across metrics, and a column in the table |
| `direction` | yes | n/a | `max` or `min` |
| `role` | no | `objective` | `objective` or `constraint_only`. At least one metric must be an `objective`. |
| `sign` | no | `none` | `none`, `negative`, `positive` or `auto`. Anything but `none` requires `direction = "min"`. |
| `eps` | no | `0.0` | A finite number ≥ 0, in the metric's units (for magnitude metrics, units of the magnitude) |
| `group` | no | none | Non-empty string. Objectives only. |
| `requires_positive` | no | none | Name of another declared metric. A constraint must guarantee that metric is > 0 (§5, C13). |

`[[constraint]]`

| Field | Required | Rule |
|---|---|---|
| `metric` | yes | A declared metric |
| `op` | yes | `<=`, `<`, `>=` or `>` |
| `value` | yes | A finite number. For a magnitude metric it must be ≥ 0. |

### Sign conventions and direction

`sign` describes how the value is stored. It applies only to metrics where smaller magnitude is
better (`direction = "min"`), such as drawdown:

| `sign` | Meaning | Checked |
|---|---|---|
| `none` | A signed quantity whose sign carries meaning (P&L, Sortino) | nothing |
| `negative` | Stored ≤ 0 (drawdown as −20) | all valid values ≤ 0 |
| `positive` | Stored ≥ 0 (drawdown as 20) | all valid values ≥ 0 |
| `auto` | Either, decided from the data | all ≤ 0 → negative, all ≥ 0 → positive, mixed → error |

`sign` with `direction = "max"` is a config error. It would mean "maximise the magnitude", which
is never what's meant for drawdown, and allowing it would give two ways to declare one metric.
Declare drawdown as `direction = "min"` with a `sign`, never as `max` on the raw negative values.

### Weights

There is no weights section yet. Weights are equal and normalised to sum to 1:

- an ungrouped objective gets one unit of weight;
- a group of k objectives shares one unit, so each gets 1/k;
- the units are divided by their total, so the weights sum to 1.

The score is therefore a weighted average of the gaps. Front-1 scores lie in [0, 1] whatever the
number of metrics, and with equal weights the ranking is exactly that of a plain sum. A `weight`
field can be added later without changing anything else.

## 5. Config errors

Every check below runs before the data is read. Each failure is an error that names the field and
the offending value.

| # | Error |
|---|---|
| C1 | `[selection]` missing, or `n` missing / not an integer / < 1 |
| C2 | `id_column` empty or not a string |
| C3 | No `[[metric]]` blocks |
| C4 | Unknown key in any section (catches typos such as `direciton`) |
| C5 | Metric `name` missing, empty, or duplicated |
| C6 | `direction` missing or not `max` / `min` |
| C7 | `role` not `objective` / `constraint_only`, or no metric has `role = "objective"` |
| C8 | `sign` not `none` / `negative` / `positive` / `auto` |
| C9 | `sign` other than `none` with `direction = "max"` |
| C10 | `eps` not a number, not finite, or < 0 |
| C11 | `group` not a non-empty string, or set on a `constraint_only` metric |
| C12 | `id_column` equals a metric name |
| C13 | `requires_positive` names an undeclared metric, the metric itself, or a metric with a sign convention (its constraint would bound \|x\|, not x), or no constraint guarantees that metric > 0. Accepted guarantees: `op = ">"` with `value ≥ 0`, or `op = ">="` with `value > 0`. |
| C14 | Constraint `metric` not declared |
| C15 | Constraint `op` not one of `<=`, `<`, `>=`, `>` |
| C16 | Constraint `value` not a finite number |
| C17 | Constraint on a magnitude metric (sign ≠ `none`) with a value < 0 |
| C18 | Constraints on the same metric that no value can satisfy together (e.g. `<= 10` and `> 20`) |

## 6. Data errors and rejections

Errors stop the run. Rejections remove one variant, with a recorded reason, and the run continues.

| # | Case | Outcome |
|---|---|---|
| D1 | `table` is not a DataFrame, or has no rows | Error |
| D2 | ID column missing | Error |
| D3 | An ID is null | Error |
| D4 | Duplicate IDs | Error |
| D5 | Declared metric column missing | Error |
| D6 | Metric column not numeric (booleans count as non-numeric) | Error |
| D7 | NaN in a metric | **Variant rejected.** Status `invalid`, reason `NaN in <metric>` |
| D8 | ±inf in a metric (e.g. `ranking.sortino` returns inf when there are no losing days) | **Variant rejected.** Status `invalid`, reason `inf in <metric>` |
| D9 | Sign convention violated, or `auto` sees mixed signs (valid rows only) | Error naming the metric and the offending variants |
| D10 | All values of an `auto` metric are exactly 0 | Passes. Resolved as `positive`, which gives the same magnitude. |
| D11 | Every variant rejected by D7 / D8 | Empty selection, with a warning |

The ID column is converted to strings for ordering and tie-breaks, so the order is the same
whatever type the IDs were stored as.

## 7. Canonical form and Pareto sorting

### Canonical form

| Spec | Canonical value `u` | Natural value (used by constraints) |
|---|---|---|
| `direction = "max"` | `u = x` | `x` |
| `direction = "min"`, `sign = "none"` | `u = -x` | `x` |
| `direction = "min"`, `sign` ≠ `none` | `u = -abs(x)` | `abs(x)` |

Drawdown stored as `-20` and drawdown stored as `20` both become `u = -20`. The results are
identical.

### Dominance

After canonicalisation every objective is "higher is better", so dominance is plain `>=` / `>`
comparisons. A dominates B if and only if:

1. `u_A[i] >= u_B[i]` for **every** objective i (at least as good everywhere), **and**
2. `u_A[j] > u_B[j] + eps[j]` for **at least one** objective j (clearly better somewhere).

`eps` comes from the config and defaults to 0. **All `eps` are 0 for now**, which is exactly strict
Pareto dominance. ε only appears in condition 2, deliberately: loosening
condition 1 too (the textbook ε-dominance) can create cycles and an empty front.

Example (ε = 0, drawdown already canonical):

| | Return | −\|DD\| | Sharpe |
|---|---|---|---|
| A | 25 | −10 | 1.6 |
| B | 22 | −12 | 1.6 |
| C | 28 | −15 | 1.4 |

A dominates B: it's ≥ everywhere and > on return and drawdown. A and C don't dominate each other.
Front 1 = {A, C}, front 2 = {B}.

### Fronts

Front 1 is every feasible variant that nothing dominates. Remove it, and front 2 is what is
non-dominated among the rest, and so on. Each dominated variant records one variant that dominates
it: the dominator with the lowest front, ties broken by ID.

Constraint-only metrics take no part in dominance.

## 8. Normalisation and scoring

1. Ideal: `ideal[i] = max u[i]` over front 1. Nadir: `nadir[i] = min u[i]` over front 1.
   Range: `range[i] = ideal[i] - nadir[i]`.
2. If `range[i] = 0`, use metric i's range over all feasible variants instead. If that is also 0,
   `d[i] = 0` for every variant, with a warning.
3. Gap: `d[i] = (ideal[i] - u[i]) / range[i]`. 0 = best on that metric, 1 = worst on front 1.
   Variants on later fronts can have gaps above 1.
4. Score: `score = sum_i w[i] * d[i]`, with weights summing to 1 (§4). **Lower is better.**
5. Order: by front first (every front-1 variant ranks above every front-2 variant), then by score
   rounded to 1e-12, then by variant ID.
6. Select the first `n`. If fewer than `n` variants are feasible, select all of them, with a
   warning.

The score is fully compensatory: a big gap on one metric can be bought back by small gaps
elsewhere. That's accepted for now. Switching to Chebyshev (`max_i w[i] * d[i]`) would change only
step 4.

### Worked example

Front 1, with drawdown stored as negative and equal weights (1/3 each):

| | Return (max) | Max DD (min, sign negative) | Sharpe (max) |
|---|---|---|---|
| A | 30 | −20 | 1.5 |
| B | 22 | −10 | 1.6 |
| C | 26 | −14 | 1.4 |
| D | 18 | −8 | 1.2 |

DD canonical: A −20, B −10, C −14, D −8. Ideal (30, −8, 1.6). Nadir (18, −20, 1.2).
Range (12, 12, 0.4).

| | d_Return | d_DD | d_Sharpe | Score | Rank |
|---|---|---|---|---|---|
| B | 0.667 | 0.167 | 0.00 | 0.278 | 1 |
| A | 0.000 | 1.000 | 0.25 | 0.417 | 2 |
| C | 0.333 | 0.500 | 0.50 | 0.444 | 3 |
| D | 1.000 | 0.000 | 1.00 | 0.667 | 4 |

With `n = 2`, the selection is B and A.

## 9. Metric redundancy and groups

Two metrics that measure the same thing get that thing counted twice in the score. Sharpe and
Sortino, for example, are both roughly return divided by risk, and across variants of a sweep they
usually rise and fall together.

Example. A has a good drawdown and weaker ratios. B has good ratios and a bad drawdown:

| | gap Sharpe | gap Sortino | gap DD |
|---|---|---|---|
| A | 1 | 1 | 0 |
| B | 0 | 0 | 1 |

- Objectives [Sharpe, DD]: A = 0.5, B = 0.5. A tie, which is fair: each is best on one aspect.
- Objectives [Sharpe, Sortino, DD]: A = 0.667, B = 0.333. **B wins**, only because return/risk now
  appears twice and drawdown once. Sortino added almost no new information but doubled the weight
  on return/risk.

**The fix is the `group` field.** Metrics in one group share one unit of weight. Grouping Sharpe and
Sortino gives them 1/4 each and DD 1/2, and A and B tie again.

The selector does **not** detect redundancy itself, and never drops a metric. Deciding whether two
metrics measure the same thing is part of choosing the config. The default three metrics (P&L,
drawdown, Sortino) measure different things and are not grouped.

## 10. Output

### Audit: one row per input variant

| Column | Content |
|---|---|
| ID | The variant ID |
| `status` | `selected` / `not_selected` / `dominated` / `infeasible` / `invalid` |
| `reason` | Why: `NaN in X` / `inf in X` / the failed constraint(s) / `dominated by <ID>` / empty |
| `front` | Front number (feasible variants only) |
| `gap_<metric>` | Gap for each objective (feasible variants only) |
| `score` | Weighted average of the gaps (feasible variants only) |
| `rank` | Rank among feasible variants |

`dominated` means a front-2-or-later variant that wasn't needed to fill the top `n`. A variant on
a later front that *was* used to fill the top `n` has the status `selected`.

### Run information

- the resolved sign of each metric (`auto` resolved to `negative` / `positive`);
- the ε of each metric (all 0 for now);
- the weights;
- the ideal and nadir points, and any range fallbacks;
- warnings (D11, range fallbacks, `n` larger than the feasible set);
- the constraint diagnostic when nothing is feasible: for each constraint, how many variants it
  removes on its own, and the best natural value among the variants against the threshold.
  Nothing is relaxed automatically.

## 11. Tests

- The worked example in §8 selects B and A.
- Drawdown stored as −20 and as +20 gives identical output.
- Mixed signs under `auto` raise an error.
- Each config error C1–C18 raises.
- NaN and inf rows are rejected, with their reasons.
- An empty feasible set returns the diagnostic.
- A zero range on front 1 triggers the fallback.
- Front 2 fills the selection when front 1 has fewer than `n`.
- Shuffling the row order doesn't change the output.
- Integer and string IDs order the same way.

## 12. Deferred

- **Estimating ε from the data.** All `eps` are 0 for now. The candidate is noise estimation from
  second differences along an ordered sweep axis. It is still open, because it can't tell genuine
  structure from noise, and with few grid points (5 points → 3 differences) the estimate is
  unstable. A later estimator will write the same per-metric `eps` values; the selector won't
  change. A paired bootstrap on daily P&L is the more principled option, if daily curves are
  supplied.
- **Diversity among the top `n`** (off for now): a greedy pick in score order that skips any
  candidate too close to one already chosen. "Close" means daily P&L correlation above a threshold,
  or parameter spacing below a minimum.
- **Minimum trades / sample-length constraint.** Not configured now. See D8 for the consequence.
- **Explicit weights** and **Chebyshev scoring** (§4, §8).
- **Redundancy warning:** flag ungrouped objectives whose rank correlation across variants is very
  high (e.g. |ρ| > 0.9). Left out of v1 because it isn't needed for the selection itself (§9).
- **Wiring** into the dashboard and the engine, and the multi-stage workflow (per-axis selection,
  then combining, re-backtesting and re-selecting).
