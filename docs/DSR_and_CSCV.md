# Deflated Sharpe Ratio and CSCV in the analysis dashboard

2026-10-08 · @Samaraha

Two overfitting-aware metrics added to the per-axis, per-weekday rankings of
`dashboards/analyse_run`: the **Deflated Sharpe Ratio (DSR)** and a
**CSCV consistency score**, plus each table's **Probability of Backtest
Overfitting (PBO)**. This note says what they are, the exact formulas used, how
they were checked against the source papers, and where they are wired in.

## Why

An axis table ranks 10–40 variants of one parameter on one weekday and keeps
the best. With that many tries, the best can look good by luck alone, and a
variant can look good because of one stretch of the period. P&L, drawdown and
Sortino can't tell either apart. The DSR discounts a variant's Sharpe for the
number of variants tried and for fat tails; CSCV checks that a variant's edge
holds across sub-periods.

## Where they are used — and where not

| Step | Ranked on | Selector config |
|---|---|---|
| Axis tables: detail view, Summary, and Combine step 1 (each axis's top n per weekday) | Final P&L, Max drawdown, Sortino, **DSR**, **CSCV**; the table's **PBO** shown | `selector/axis.toml` |
| Combine round 1 best-of (H\*, S\*) and the final recommendation | Final P&L, Max drawdown, Sortino — **unchanged** | `selector/default.toml` |

The Combine pools are left out on purpose. They hold 4–25 candidates that were
*already chosen* from the axis tables on the same data, so: the DSR's
threshold would count far too few trials and overstate significance; the
pools are too small for a stable PBO; and no part of the period is untouched by
the earlier selection, so PBO would be biased low.

Every computation is within **one table** — one axis and one weekday, or the
set being ranked. Weekdays are never mixed.

## 1. Deflated Sharpe Ratio

**Source.** D. H. Bailey and M. López de Prado (2014), *The Deflated Sharpe
Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality*,
Journal of Portfolio Management 40(5), 94–107, eq. (2) and Appendix A.1.
[SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)

**What it is.** The probability that a variant's true Sharpe ratio exceeds the
Sharpe the best of N skill-less variants would show by luck, corrected for
sample length, skewness and kurtosis.

**Formula (eq. 2).**

$$
\mathrm{DSR} = Z\!\left[\frac{(\widehat{SR} - SR_0)\sqrt{T-1}}{\sqrt{1 - \hat\gamma_3\,\widehat{SR} + \frac{\hat\gamma_4 - 1}{4}\,\widehat{SR}^2}}\right]
$$

$$
SR_0 = \sqrt{V[\{\widehat{SR}_n\}]}\,\Big((1-\gamma)\,Z^{-1}\big[1 - \tfrac{1}{N}\big] + \gamma\,Z^{-1}\big[1 - \tfrac{1}{N e}\big]\Big)
$$

| Symbol | Meaning | How we compute it |
|---|---|---|
| Z, Z⁻¹ | standard Normal CDF and its inverse | `statistics.NormalDist` |
| $\widehat{SR}$ | the variant's Sharpe ratio, **per observation** (not annualised) | mean ÷ sample std (ddof 1) of its daily P&L **on days it has P&L** |
| T | sample length | number of those days |
| $\hat\gamma_3$, $\hat\gamma_4$ | skewness and **raw** kurtosis (3 for a Normal) | moment estimators m₃/m₂^1.5 and m₄/m₂² on those days |
| N | number of trials | **number of variants in the table** |
| V | variance of the trials' Sharpe ratios | sample variance (ddof 1) of the table's $\widehat{SR}$ |
| γ | Euler–Mascheroni constant | 0.5772156649 |
| SR₀ | expected maximum Sharpe of N trials when every true Sharpe is 0 | as above; 0 when N < 2 |

How to read it: about 0.5 means no better than the luckiest of the table's
variants; 0.95 means a 95% chance the true Sharpe beats that bar. More
variants or a wider spread of results raise the bar; negative skew and fat
tails widen the uncertainty and lower the DSR.

Within one table SR₀ and the variance are shared, so without the skewness and
kurtosis term the DSR would just repeat the Sharpe order. The tail term — each
variant's own — and each variant's own T are what make it informative.

## 2. CSCV and PBO

**Source.** D. H. Bailey, J. M. Borwein, M. López de Prado and Q. J. Zhu
(2015), *The Probability of Backtest Overfitting*, Journal of Computational
Finance, Algorithm 2.3 and §3.1.
[SSRN 2326253](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)

**Algorithm 2.3, as implemented** (S = 6):

1. **M** — a T × N matrix: the table's N variants' daily P&L on their common
   calendar (0 on a day a variant has no P&L). Rows beyond a multiple of S are
   dropped from the end.
2. Split M's rows into **S = 6** equal blocks in time order.
3. Form all C(6, 3) = **20** ways of taking 3 blocks as in-sample (IS); the
   other 3, in order, are out-of-sample (OOS). Each block is OOS in 10 of them.
4. For each split: Sharpe of every variant on IS and on OOS; OOS relative rank
   ω = rank / (N + 1) with rank 1 = worst, ties averaged; take the IS-best
   variant n\* and its logit λ = ln(ω\_{n\*} / (1 − ω\_{n\*})).

**PBO (the paper's output, per table)** = the share of the 20 splits with
λ ≤ 0 — how often the in-sample winner lands at or below the out-of-sample
median. 0 means the table's ranking carries over; around 0.5 means it is no
better than chance. Shown above each axis table and as "Table PBO" in the
Summary.

**CSCV score (per variant, our adaptation)** = the variant's average OOS
relative rank ω over the 20 splits, in (0, 1), higher is better. The paper
computes ω only for the in-sample winner; applying the same ω to every variant
gives a consistency score for ranking. It is not the PBO.

**Why S = 6.** About 120 trading days give blocks of ~20 days — ~4 trading
days per block for a single-weekday strategy — and 20 splits. It costs ~1 ms a
table; S = 16 would cost ~0.6 s a table with blocks too thin to estimate a
Sharpe.

## Scaling

Both metrics are ratios. Multiplying a variant's P&L by its margin factor
cancels out of every Sharpe, rank, skewness and kurtosis, so they are computed
on raw daily P&L. Only Final P&L and Max drawdown need margin scaling.

## Weighting

Sortino and DSR both measure return per unit of risk, so they share one unit
of weight instead of counting that idea twice.

| Metric | Composite weight (on ranks) | Selector weight (`axis.toml`) |
|---|---|---|
| Final P&L | 1 | 25% |
| Max drawdown | 1 | 25% |
| CSCV | 1 | 25% |
| Sortino | ½ | 12.5% (group "return per unit of risk") |
| DSR | ½ | 12.5% (same group) |

## Edge cases

- A variant with fewer than 3 days of P&L or no variance gets no Sharpe, so a
  blank DSR; the selector rejects it as `invalid` (reason "NaN in DSR").
- Variants whose basic metrics are blank (e.g. zero margin) take no part in N,
  V or the CSCV matrix.
- Fewer than 2 variants, or fewer than 12 days: no CSCV score and no PBO.
- With a narrowed date range, everything is computed on the window.

## Code

| File | Role |
|---|---|
| `dashboards/analyse_run/robustness.py` | the maths: `moments`, `expected_max_sharpe`, `deflated_sharpe`, `dsr_table`, `cscv`, `cscv_table` |
| `dashboards/analyse_run/ranking.py` | `table(..., robust=True)` adds `DSR`, `CSCV`, their ranks and `attrs["PBO"]`; `ROBUST_WEIGHTS`; picks `axis.toml` for tables carrying DSR and CSCV |
| `dashboards/analyse_run/selector/axis.toml` | selector config for the axis tables |
| `dashboards/analyse_run/summary.py`, `panels.py`, `streamlit_app.py` | axis tables call `robust=True`; PBO and the new columns are shown |
| `dashboards/analyse_run/combination_page.py` | unchanged — Combine tables use `robust=False` and `default.toml` |
| `dashboards/analyse_run/test_robustness.py` | tests, below |

## Verification

`python -m unittest test_robustness -v` (from `dashboards/analyse_run/`), 22
tests:

- **The DSR paper's numerical example** (annualised SR 2.5, T = 1,250, N = 100,
  V = ½, skewness −3, kurtosis 10) gives **0.9004**; N = 46 gives **0.9505**;
  Normal returns at N = 88 give **0.9505** — the values the paper reports.
- SR₀ equals the paper's own `getExpMaxSR` (Appendix A.2, Snippet 1) and a
  Monte Carlo of the expected maximum of N Normals for N = 10, 100, 1000.
- N = 1 reduces to the Probabilistic Sharpe Ratio with the Normal standard
  error √((1 + SR²/2)/(T−1)); more trials lower the DSR; negative skew and fat
  tails lower it; the variance term stays positive (Pearson's inequality).
- **CSCV** matches a step-by-step re-implementation of Algorithm 2.3 on 25
  random matrices; 20 splits, each block OOS 10 times; pure noise gives PBO ≈
  0.5; persistent skill gives PBO = 0 and scores in skill order.
- Both are unchanged when P&L is rescaled.

Checked against the real NIFTY and SENSEX sweeps (all 50 axis × weekday
tables): the table's DSR, CSCV and PBO equal `robustness.py` on the same data;
P&L, drawdown and Sortino are unchanged; Combine-style tables are identical to
before; the selector uses `axis.toml` or `default.toml` as intended.
