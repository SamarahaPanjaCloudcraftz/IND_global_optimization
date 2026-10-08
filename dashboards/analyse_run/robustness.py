"""Overfitting-aware metrics for one ranking table: the Deflated Sharpe Ratio
and combinatorially symmetric cross-validation (CSCV). See docs/DSR_and_CSCV.md.

    DSR  Bailey & López de Prado (2014), "The Deflated Sharpe Ratio: Correcting
         for Selection Bias, Backtest Overfitting and Non-Normality", Journal of
         Portfolio Management 40(5), eq. (2).
    CSCV Bailey, Borwein, López de Prado & Zhu (2015), "The Probability of
         Backtest Overfitting", Journal of Computational Finance, Algorithm 2.3.

Both are computed within one table — the set of variants being ranked
together — and both are ratios, so a variant's margin scaling cancels out.
Standard library and numpy only.
"""

import itertools
import math
from statistics import NormalDist

import numpy as np
import pandas as pd

EULER_MASCHERONI = 0.5772156649
CSCV_BLOCKS = 6        # S: even number of equal row blocks
_Z = NormalDist()


# ------------------------------------------------------------------ DSR

def moments(x: np.ndarray) -> tuple[float, float, float, float]:
    """(Sharpe, T, skewness, raw kurtosis) of a P&L series, per observation.

    Sharpe uses the sample standard deviation. Skewness and kurtosis are the
    moment estimators m3/m2^1.5 and m4/m2^2; kurtosis is raw, 3 for a Normal,
    as eq. (2) expects.
    """
    x = np.asarray(x, dtype=float)
    t = len(x)
    if t < 3:
        return math.nan, t, math.nan, math.nan
    sd = x.std(ddof=1)
    if sd == 0:
        return math.nan, t, math.nan, math.nan
    d = x - x.mean()
    m2 = (d ** 2).mean()
    return float(x.mean() / sd), t, float((d ** 3).mean() / m2 ** 1.5), float((d ** 4).mean() / m2 ** 2)


def expected_max_sharpe(variance: float, trials: int) -> float:
    """SR0 of eq. (2): the expected maximum of `trials` Sharpe ratios drawn
    with the given variance when every true Sharpe is zero. One trial is no
    multiple testing, so the threshold is zero."""
    if trials < 2 or not variance > 0:
        return 0.0
    g = EULER_MASCHERONI
    z = (1 - g) * _Z.inv_cdf(1 - 1 / trials) + g * _Z.inv_cdf(1 - 1 / (trials * math.e))
    return math.sqrt(variance) * z


def deflated_sharpe(sharpe: float, sr0: float, t: int, skew: float, kurtosis: float) -> float:
    """Eq. (2): P[true Sharpe > SR0], corrected for sample length and for the
    skewness and raw kurtosis of the returns."""
    if not all(math.isfinite(v) for v in (sharpe, sr0, skew, kurtosis)) or t < 2:
        return math.nan
    spread = 1 - skew * sharpe + (kurtosis - 1) / 4 * sharpe ** 2
    if spread <= 0:
        return math.nan
    return _Z.cdf((sharpe - sr0) * math.sqrt(t - 1) / math.sqrt(spread))


def dsr_table(daily: dict[str, pd.Series]) -> tuple[dict[str, float], float]:
    """DSR of every variant in one table, and the table's SR0.

    Each variant's Sharpe, skewness and kurtosis come from its daily P&L on
    the days it has P&L. N is the number of variants in the table; V is the
    sample variance of their Sharpe ratios.
    """
    stats = {k: moments(s[s != 0].to_numpy()) for k, s in daily.items()}
    sharpes = [m[0] for m in stats.values() if math.isfinite(m[0])]
    variance = float(np.var(sharpes, ddof=1)) if len(sharpes) > 1 else 0.0
    sr0 = expected_max_sharpe(variance, len(daily))
    return {k: deflated_sharpe(sr, sr0, t, sk, ku) for k, (sr, t, sk, ku) in stats.items()}, sr0


# ------------------------------------------------------------------ CSCV

def _sharpe_columns(m: np.ndarray) -> np.ndarray:
    sd = m.std(axis=0, ddof=1)
    return np.divide(m.mean(axis=0), sd, out=np.zeros(m.shape[1]), where=sd > 0)


def _relative_rank(x: np.ndarray) -> np.ndarray:
    """Rank 1 = worst .. N = best, ties averaged, divided by N + 1."""
    return pd.Series(x).rank(method="average").to_numpy() / (len(x) + 1)


def cscv(matrix: np.ndarray, blocks: int = CSCV_BLOCKS) -> tuple[np.ndarray, float]:
    """Algorithm 2.3 on a T x N P&L matrix (rows synchronous, one column per
    variant), with Sharpe as the performance statistic.

    Returns each variant's average out-of-sample relative rank over all
    C(S, S/2) splits — the paper's relative rank applied to every variant, a
    consistency score in (0, 1), higher is better — and the PBO: the share of
    splits in which the in-sample best ranks at or below the median out of
    sample (logit <= 0). Rows beyond a multiple of S are dropped from the end.
    """
    t, n = matrix.shape
    t -= t % blocks
    if n < 2 or t < blocks * 2:
        return np.full(n, np.nan), math.nan
    parts = np.split(matrix[:t], blocks)
    oos_sum, logits = np.zeros(n), []
    splits = list(itertools.combinations(range(blocks), blocks // 2))
    for train in splits:
        test = [i for i in range(blocks) if i not in train]
        is_sharpe = _sharpe_columns(np.vstack([parts[i] for i in train]))
        oos_rank = _relative_rank(_sharpe_columns(np.vstack([parts[i] for i in test])))
        oos_sum += oos_rank
        best = int(np.argmax(is_sharpe))
        logits.append(math.log(oos_rank[best] / (1 - oos_rank[best])))
    return oos_sum / len(splits), float(np.mean(np.array(logits) <= 0))


def cscv_table(daily: dict[str, pd.Series], blocks: int = CSCV_BLOCKS) -> tuple[dict[str, float], float]:
    """CSCV score of every variant in one table, and the table's PBO. The
    matrix is the variants' daily P&L on their common calendar, 0 on a day a
    variant has no P&L."""
    if not daily:
        return {}, math.nan
    frame = pd.DataFrame(daily).sort_index().fillna(0.0)
    scores, pbo = cscv(frame.to_numpy(), blocks)
    return dict(zip(frame.columns, scores)), pbo
