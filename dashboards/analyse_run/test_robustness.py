"""Tests for robustness.py against the source papers (docs/DSR_and_CSCV.md).

Run from analyse_run/:  python -m unittest test_robustness -v
"""

import itertools
import math
import unittest
from statistics import NormalDist

import numpy as np
import pandas as pd

import robustness as R

Z = NormalDist()


class DeflatedSharpe(unittest.TestCase):
    """Bailey & López de Prado (2014), eq. (2) and its numerical example."""

    # The paper's example: annualised SR 2.5 on 5 years of daily data, 250
    # observations a year, V[SR] = 1/2 annualised, skewness -3, kurtosis 10.
    SR = 2.5 / math.sqrt(250)
    VAR = 0.5 / 250
    T = 1250

    def test_paper_example_n100(self):
        sr0 = R.expected_max_sharpe(self.VAR, 100)
        self.assertAlmostEqual(R.deflated_sharpe(self.SR, sr0, self.T, -3, 10), 0.9004, places=4)

    def test_paper_example_n46_crosses_95pct(self):
        sr0 = R.expected_max_sharpe(self.VAR, 46)
        self.assertAlmostEqual(R.deflated_sharpe(self.SR, sr0, self.T, -3, 10), 0.9505, places=4)

    def test_paper_example_normal_returns_n88(self):
        sr0 = R.expected_max_sharpe(self.VAR, 88)
        self.assertAlmostEqual(R.deflated_sharpe(self.SR, sr0, self.T, 0, 3), 0.9505, places=4)

    def test_expected_max_matches_paper_snippet(self):
        """Appendix A.2, Snippet 1, getExpMaxSR with mu = 0."""
        emc = 0.5772156649
        for n in (2, 10, 46, 100, 1000):
            snippet = (1 - emc) * Z.inv_cdf(1 - 1 / n) + emc * Z.inv_cdf(1 - 1 / (n * math.e))
            self.assertAlmostEqual(R.expected_max_sharpe(1.0, n), snippet, places=12)

    def test_expected_max_matches_simulation(self):
        """Appendix A.2: the analytic expected maximum against Monte Carlo."""
        rng = np.random.default_rng(7)
        for n in (10, 100, 1000):
            simulated = rng.standard_normal((20_000, n)).max(axis=1).mean()
            self.assertAlmostEqual(R.expected_max_sharpe(1.0, n), simulated, delta=0.05 * simulated)

    def test_one_trial_is_the_probabilistic_sharpe_ratio(self):
        """N = 1: no multiple testing, threshold 0; with Normal returns the
        spread is 1 + SR^2/2 (Lo 2002)."""
        self.assertEqual(R.expected_max_sharpe(0.01, 1), 0.0)
        sr, t = 0.1, 200
        self.assertAlmostEqual(R.deflated_sharpe(sr, 0.0, t, 0, 3),
                               Z.cdf(sr * math.sqrt(t - 1) / math.sqrt(1 + sr ** 2 / 2)), places=12)

    def test_more_trials_lower_dsr(self):
        values = [R.deflated_sharpe(self.SR, R.expected_max_sharpe(self.VAR, n), self.T, -3, 10)
                  for n in (2, 10, 100, 1000)]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_negative_skew_and_fat_tails_lower_dsr(self):
        sr0 = R.expected_max_sharpe(self.VAR, 100)
        normal = R.deflated_sharpe(self.SR, sr0, self.T, 0, 3)
        self.assertLess(R.deflated_sharpe(self.SR, sr0, self.T, -3, 10), normal)

    def test_moments(self):
        x = np.array([1.0, -2.0, 3.0, 0.5, -1.0, 4.0])
        d = x - x.mean(); m2 = (d ** 2).mean()
        sr, t, skew, kurt = R.moments(x)
        self.assertAlmostEqual(sr, x.mean() / x.std(ddof=1))
        self.assertEqual(t, 6)
        self.assertAlmostEqual(skew, (d ** 3).mean() / m2 ** 1.5)
        self.assertAlmostEqual(kurt, (d ** 4).mean() / m2 ** 2)
        normal = np.random.default_rng(1).standard_normal(200_000)
        _, _, s, k = R.moments(normal)
        self.assertAlmostEqual(s, 0.0, delta=0.02)
        self.assertAlmostEqual(k, 3.0, delta=0.05)     # raw kurtosis

    def test_spread_term_stays_positive(self):
        """Pearson's inequality (kurt >= skew^2 + 1) keeps 1 - skew*SR +
        (kurt-1)/4*SR^2 positive for any sample."""
        rng = np.random.default_rng(3)
        for _ in range(500):
            x = rng.lognormal(0, rng.uniform(0.1, 2), 60) * rng.choice([-1, 1])
            sr, t, skew, kurt = R.moments(x)
            self.assertGreater(1 - skew * sr + (kurt - 1) / 4 * sr ** 2, 0)

    def test_degenerate_series(self):
        self.assertTrue(math.isnan(R.moments(np.array([1.0, 2.0]))[0]))     # too short
        self.assertTrue(math.isnan(R.moments(np.ones(10))[0]))              # no variance
        self.assertTrue(math.isnan(R.deflated_sharpe(math.nan, 0.0, 50, 0, 3)))

    def test_table_uses_its_own_n_and_variance(self):
        rng = np.random.default_rng(5)
        days = pd.date_range("2026-01-01", periods=120, freq="B")
        daily = {f"v{i}": pd.Series(rng.normal(0.05 * i, 1, 120), index=days) for i in range(8)}
        dsr, sr0 = R.dsr_table(daily)
        sharpes = [R.moments(s.to_numpy())[0] for s in daily.values()]
        self.assertAlmostEqual(sr0, R.expected_max_sharpe(np.var(sharpes, ddof=1), 8))
        for k, s in daily.items():
            sr, t, sk, ku = R.moments(s.to_numpy())
            self.assertAlmostEqual(dsr[k], R.deflated_sharpe(sr, sr0, t, sk, ku))

    def test_table_uses_days_with_pnl_only(self):
        days = pd.date_range("2026-01-01", periods=10, freq="B")
        a = pd.Series([0, 1.0, 0, -0.5, 0, 2.0, 0, 0.3, 0, 1.1], index=days)
        b = pd.Series([0.4, -0.2, 0.9, 0.1, -0.3, 0.6, 0.2, -0.1, 0.5, 0.3], index=days)
        dsr, sr0 = R.dsr_table({"a": a, "b": b})
        sr, t, sk, ku = R.moments(a[a != 0].to_numpy())
        self.assertEqual(t, 5)
        self.assertAlmostEqual(dsr["a"], R.deflated_sharpe(sr, sr0, t, sk, ku))

    def test_scale_invariant(self):
        rng = np.random.default_rng(9)
        days = pd.date_range("2026-01-01", periods=120, freq="B")
        daily = {f"v{i}": pd.Series(rng.normal(0.1, 1, 120), index=days) for i in range(5)}
        scaled = {k: s * (i + 1) * 7.3 for i, (k, s) in enumerate(daily.items())}
        a, b = R.dsr_table(daily)[0], R.dsr_table(scaled)[0]
        for k in a:
            self.assertAlmostEqual(a[k], b[k], places=10)


def _cscv_reference(m: np.ndarray, s: int):
    """Algorithm 2.3 written out step by step, independently of robustness.cscv."""
    t, n = m.shape
    t -= t % s
    size = t // s
    blocks = [m[i * size:(i + 1) * size] for i in range(s)]          # second: S row blocks
    def sharpe(x):
        out = []
        for j in range(x.shape[1]):
            col = x[:, j]; sd = col.std(ddof=1)
            out.append(col.mean() / sd if sd > 0 else 0.0)
        return np.array(out)
    def rank(x):                                                     # 1 = worst, ties averaged
        order = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x); i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return np.array(r)
    total, logits = np.zeros(n), []
    combos = list(itertools.combinations(range(s), s // 2))           # third: C(S, S/2)
    for c in combos:                                                  # fourth: per combination
        train = np.vstack([blocks[i] for i in c])                     # a) J, in original order
        test = np.vstack([blocks[i] for i in range(s) if i not in c]) # b) the complement
        r_is, r_oos = sharpe(train), sharpe(test)                     # c), d)
        omega = rank(r_oos) / (n + 1)
        total += omega
        best = int(np.argmax(r_is))                                   # e) best in sample
        logits.append(math.log(omega[best] / (1 - omega[best])))      # f), g)
    return total / len(combos), sum(l <= 0 for l in logits) / len(logits), len(combos)


class CSCV(unittest.TestCase):
    """Bailey, Borwein, López de Prado & Zhu (2015), Algorithm 2.3."""

    def test_matches_step_by_step_algorithm(self):
        rng = np.random.default_rng(11)
        for trial in range(25):
            m = rng.normal(rng.normal(0, 0.05, 12), 1, (123, 12))
            m[rng.random(m.shape) < 0.6] = 0.0                       # most days without P&L
            score, pbo = R.cscv(m, 6)
            ref_score, ref_pbo, splits = _cscv_reference(m, 6)
            self.assertEqual(splits, 20)
            np.testing.assert_allclose(score, ref_score, rtol=0, atol=1e-12)
            self.assertAlmostEqual(pbo, ref_pbo, places=12)

    def test_every_block_out_of_sample_half_the_time(self):
        combos = list(itertools.combinations(range(6), 3))
        self.assertEqual(len(combos), math.comb(6, 3))
        for b in range(6):
            self.assertEqual(sum(b not in c for c in combos), 10)

    def test_noise_gives_pbo_near_one_half(self):
        """No skill anywhere: the in-sample winner's out-of-sample rank is
        uniform, so PBO averages about 0.5."""
        rng = np.random.default_rng(13)
        pbos = [R.cscv(rng.standard_normal((120, 20)), 6)[1] for _ in range(300)]
        self.assertAlmostEqual(float(np.mean(pbos)), 0.5, delta=0.06)

    def test_persistent_skill_gives_pbo_near_zero(self):
        rng = np.random.default_rng(17)
        m = rng.standard_normal((120, 20)) + np.linspace(0, 1.5, 20)  # skill rises column by column
        score, pbo = R.cscv(m, 6)
        self.assertEqual(pbo, 0.0)
        # the consistency score follows the true skill order
        spearman = pd.Series(score).rank().corr(pd.Series(range(20), dtype=float).rank())
        self.assertGreater(spearman, 0.95)
        self.assertGreaterEqual(int(np.argmax(score)), 17)

    def test_drops_rows_beyond_a_multiple_of_s(self):
        rng = np.random.default_rng(19)
        m = rng.standard_normal((125, 5))
        np.testing.assert_allclose(R.cscv(m, 6)[0], R.cscv(m[:120], 6)[0])

    def test_scale_invariant(self):
        rng = np.random.default_rng(23)
        m = rng.standard_normal((120, 6))
        a, pa = R.cscv(m, 6)
        b, pb = R.cscv(m * np.array([1, 3, 0.5, 9, 2, 7]), 6)
        np.testing.assert_allclose(a, b); self.assertEqual(pa, pb)

    def test_too_few_variants_or_rows(self):
        self.assertTrue(np.isnan(R.cscv(np.ones((120, 1)), 6)[0]).all())
        self.assertTrue(math.isnan(R.cscv(np.ones((10, 4)), 6)[1]))

    def test_table_aligns_on_the_common_calendar(self):
        days = pd.date_range("2026-01-01", periods=60, freq="B")
        rng = np.random.default_rng(29)
        a = pd.Series(rng.normal(size=60), index=days)
        b = pd.Series(rng.normal(size=30), index=days[::2])           # half the days
        score, pbo = R.cscv_table({"a": a, "b": b})
        m = pd.DataFrame({"a": a, "b": b}).fillna(0.0).to_numpy()
        ref, ref_pbo = R.cscv(m, 6)
        self.assertAlmostEqual(score["a"], ref[0]); self.assertAlmostEqual(score["b"], ref[1])
        self.assertEqual(pbo, ref_pbo)


if __name__ == "__main__":
    unittest.main()
