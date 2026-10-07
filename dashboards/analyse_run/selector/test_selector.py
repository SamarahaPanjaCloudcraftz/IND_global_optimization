"""Tests for the selector (DESIGN.md §11).

Run from analyse_run/:  python -m unittest selector.test_selector -v
"""

import copy
import math
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from . import ConfigError, DataError, load, select
from .pareto import fronts

HERE = Path(__file__).parent


def config(n=2, constraints=None, **overrides):
    """Return/DD/Sharpe config, as in the worked example (DESIGN.md §8)."""
    cfg = {
        "selection": {"n": n},
        "metric": [
            {"name": "Return", "direction": "max"},
            {"name": "DD", "direction": "min", "sign": "auto"},
            {"name": "Sharpe", "direction": "max"},
        ],
        "constraint": constraints or [],
    }
    cfg.update(overrides)
    return cfg


def worked(dd_sign=-1):
    return pd.DataFrame({
        "Variant": ["A", "B", "C", "D"],
        "Return": [30.0, 22.0, 26.0, 18.0],
        "DD": [dd_sign * 20.0, dd_sign * 10.0, dd_sign * 14.0, dd_sign * 8.0],
        "Sharpe": [1.5, 1.6, 1.4, 1.2],
    })


class WorkedExample(unittest.TestCase):
    def test_selects_b_then_a(self):
        result = select(worked(), config(n=2))
        self.assertEqual(result.selected, ["B", "A"])
        scores = result.audit.set_index("Variant")["score"]
        for name, expected in {"B": 0.8333 / 3, "A": 1.25 / 3, "C": 1.3333 / 3, "D": 2 / 3}.items():
            self.assertAlmostEqual(scores[name], expected, places=3)
        self.assertEqual(list(result.audit["Variant"]), ["B", "A", "C", "D"])
        self.assertEqual(list(result.audit["status"]),
                         ["selected", "selected", "not_selected", "not_selected"])

    def test_ideal_and_nadir_in_natural_units(self):
        run = select(worked(), config()).run
        self.assertEqual(run["ideal"], {"Return": 30.0, "DD": 8.0, "Sharpe": 1.6})
        self.assertEqual(run["nadir"], {"Return": 18.0, "DD": 20.0, "Sharpe": 1.2})
        self.assertEqual(run["signs"]["DD"], "negative")

    def test_drawdown_sign_storage_does_not_matter(self):
        neg = select(worked(-1), config())
        pos = select(worked(+1), config())
        self.assertEqual(neg.selected, pos.selected)
        pd.testing.assert_frame_equal(neg.audit, pos.audit)
        self.assertEqual(pos.run["signs"]["DD"], "positive")

    def test_row_order_does_not_matter(self):
        base = select(worked(), config(n=3))
        for seed in range(5):
            shuffled = worked().sample(frac=1, random_state=seed)
            result = select(shuffled, config(n=3))
            self.assertEqual(result.selected, base.selected)
            pd.testing.assert_frame_equal(result.audit, base.audit)

    def test_toml_and_dict_agree(self):
        toml = HERE / "default.toml"
        table = pd.DataFrame({"Variant": ["x", "y", "z"], "Final P&L": [100.0, 80.0, 120.0],
                              "Max drawdown": [-30.0, -10.0, -50.0], "Sortino": [1.0, 1.2, 0.9]})
        result = select(table, toml)
        self.assertEqual(len(result.selected), 3)
        self.assertEqual(result.run["signs"]["Max drawdown"], "negative")


class Dominance(unittest.TestCase):
    def test_design_example(self):
        # DESIGN.md §7: A dominates B; A and C are mutually non-dominated.
        u = np.array([[25, -10, 1.6], [22, -12, 1.6], [28, -15, 1.4]])
        front, dominator = fronts(u, np.zeros(3))
        self.assertEqual(front.tolist(), [1, 2, 1])
        self.assertEqual(dominator.tolist(), [-1, 0, -1])

    def test_eps_keeps_near_tie(self):
        table = pd.DataFrame({"Variant": ["A", "B"], "Return": [25.0, 25.0],
                              "DD": [-10.0, -10.0], "Sharpe": [1.501, 1.500]})
        strict = select(table, config(n=1)).audit.set_index("Variant")
        self.assertEqual(strict.loc["B", "front"], 2)
        loose_cfg = config(n=1)
        loose_cfg["metric"][2]["eps"] = 0.05
        loose = select(table, loose_cfg).audit.set_index("Variant")
        self.assertEqual(loose.loc["B", "front"], 1)

    def test_front_never_empty_with_eps(self):
        rng = np.random.default_rng(0)
        for _ in range(2000):
            u = rng.normal(size=(rng.integers(2, 9), rng.integers(2, 5)))
            front, _ = fronts(u, np.full(u.shape[1], rng.uniform(0.1, 1.0)))
            self.assertTrue((front == 1).any())
            self.assertTrue((front > 0).all())


class Filling(unittest.TestCase):
    def test_front_two_fills_and_records_dominator(self):
        table = pd.DataFrame({"Variant": ["A", "B", "C"], "Return": [30.0, 20.0, 10.0],
                              "DD": [-5.0, -10.0, -15.0], "Sharpe": [2.0, 1.5, 1.0]})
        result = select(table, config(n=2))
        self.assertEqual(result.selected, ["A", "B"])
        audit = result.audit.set_index("Variant")
        self.assertEqual(audit.loc["B", "front"], 2)
        self.assertEqual(audit.loc["B", "status"], "selected")
        self.assertEqual(audit.loc["B", "reason"], "dominated by A")
        self.assertEqual(audit.loc["C", "status"], "dominated")
        self.assertEqual(audit.loc["C", "reason"], "dominated by A")

    def test_zero_range_on_front_one_falls_back(self):
        # A alone is front 1, so every front-1 range is zero.
        table = pd.DataFrame({"Variant": ["A", "B", "C"], "Return": [30.0, 20.0, 10.0],
                              "DD": [-5.0, -10.0, -15.0], "Sharpe": [2.0, 1.5, 1.0]})
        result = select(table, config(n=3))
        self.assertEqual(result.run["range_fallbacks"], ["Return", "DD", "Sharpe"])
        audit = result.audit.set_index("Variant")
        self.assertAlmostEqual(audit.loc["A", "score"], 0.0)
        self.assertAlmostEqual(audit.loc["B", "score"], 0.5)
        self.assertAlmostEqual(audit.loc["C", "score"], 1.0)

    def test_constant_metric_has_zero_gaps(self):
        table = worked().assign(Sharpe=1.0)
        result = select(table, config())
        self.assertTrue((result.audit["gap_Sharpe"] == 0).all())
        self.assertTrue(any("identical" in w for w in result.run["warnings"]))

    def test_n_larger_than_feasible(self):
        result = select(worked(), config(n=10))
        self.assertEqual(len(result.selected), 4)
        self.assertTrue(any("selecting all" in w for w in result.run["warnings"]))

    def test_single_variant(self):
        result = select(worked().head(1), config(n=2))
        self.assertEqual(result.selected, ["A"])
        self.assertEqual(result.audit.loc[0, "score"], 0.0)

    def test_ties_broken_by_id(self):
        table = pd.DataFrame({"Variant": ["b", "a"], "Return": [1.0, 1.0],
                              "DD": [-1.0, -1.0], "Sharpe": [1.0, 1.0]})
        self.assertEqual(select(table, config(n=2)).selected, ["a", "b"])

    def test_int_and_string_ids_order_the_same(self):
        as_int = worked().assign(Variant=[3, 1, 2, 10])
        as_str = worked().assign(Variant=["3", "1", "2", "10"])
        self.assertEqual([str(v) for v in select(as_int, config(n=4)).selected],
                         select(as_str, config(n=4)).selected)
        self.assertIsInstance(select(as_int, config(n=1)).selected[0], (int, np.integer))


class Groups(unittest.TestCase):
    def test_group_shares_one_unit(self):
        cfg = config()
        cfg["metric"].append({"name": "Sortino", "direction": "max", "group": "ratio"})
        cfg["metric"][2]["group"] = "ratio"
        weights = load(cfg).weights()
        self.assertEqual(weights, {"Return": 1 / 3, "DD": 1 / 3, "Sharpe": 1 / 6, "Sortino": 1 / 6})

    def test_constraint_only_metric_is_not_scored(self):
        cfg = config(constraints=[{"metric": "Trades", "op": ">=", "value": 10}])
        cfg["metric"].append({"name": "Trades", "direction": "max", "role": "constraint_only"})
        table = worked().assign(Trades=[5, 20, 20, 20])
        result = select(table, cfg)
        self.assertNotIn("gap_Trades", result.audit.columns)
        self.assertEqual(result.audit.set_index("Variant").loc["A", "status"], "infeasible")


class Rejections(unittest.TestCase):
    def test_nan_and_inf_rejected_with_reason(self):
        table = worked()
        table.loc[0, "Sharpe"] = np.nan
        table.loc[1, "Return"] = np.inf
        audit = select(table, config()).audit.set_index("Variant")
        self.assertEqual(audit.loc["A", "status"], "invalid")
        self.assertEqual(audit.loc["A", "reason"], "NaN in Sharpe")
        self.assertEqual(audit.loc["B", "reason"], "inf in Return")
        self.assertTrue(math.isnan(audit.loc["A", "score"]))

    def test_all_rejected(self):
        table = worked().assign(Sharpe=np.nan)
        result = select(table, config())
        self.assertEqual(result.selected, [])
        self.assertTrue(any(w.startswith("D11") for w in result.run["warnings"]))

    def test_constraints_on_magnitude(self):
        cons = [{"metric": "DD", "op": "<=", "value": 12}]
        for sign in (-1, 1):
            audit = select(worked(sign), config(n=4, constraints=cons)).audit.set_index("Variant")
            self.assertEqual(audit.loc["A", "status"], "infeasible")
            self.assertEqual(audit.loc["A", "reason"], "DD <= 12 (was 20)")
            self.assertEqual(audit.loc["C", "status"], "infeasible")
            self.assertEqual((audit["status"] != "infeasible").sum(), 2)

    def test_empty_feasible_set_returns_diagnostic(self):
        cons = [{"metric": "DD", "op": "<", "value": 5}, {"metric": "Return", "op": ">", "value": 25}]
        result = select(worked(), config(constraints=cons))
        self.assertEqual(result.selected, [])
        self.assertEqual(result.run["diagnostic"], [
            {"constraint": "DD < 5", "removes": 4, "best_value": 8.0},
            {"constraint": "Return > 25", "removes": 2, "best_value": 30.0},
        ])


class DataErrors(unittest.TestCase):
    def check(self, table, code, cfg=None):
        with self.assertRaises(DataError) as caught:
            select(table, cfg or config())
        self.assertIn(code, str(caught.exception))

    def test_d1_not_a_frame_or_empty(self):
        self.check([1, 2], "D1")
        self.check(worked().head(0), "D1")

    def test_d2_missing_id_column(self):
        self.check(worked().rename(columns={"Variant": "id"}), "D2")

    def test_d3_null_id(self):
        self.check(worked().assign(Variant=["A", None, "C", "D"]), "D3")

    def test_d4_duplicate_ids(self):
        self.check(worked().assign(Variant=["A", "A", "C", "D"]), "D4")
        self.check(worked().assign(Variant=[1, "1", "C", "D"]), "D4")

    def test_d5_missing_metric(self):
        self.check(worked().drop(columns="Sharpe"), "D5")

    def test_d6_non_numeric(self):
        self.check(worked().assign(Sharpe=["x", "y", "z", "w"]), "D6")
        self.check(worked().assign(Sharpe=[True, False, True, False]), "D6")

    def test_d9_sign_violations(self):
        self.check(worked().assign(DD=[-20.0, 10.0, -14.0, -8.0]), "D9")
        cfg = config()
        cfg["metric"][1]["sign"] = "negative"
        self.check(worked(+1), "D9", cfg)
        cfg["metric"][1]["sign"] = "positive"
        self.check(worked(-1), "D9", cfg)

    def test_d10_all_zero_auto_passes(self):
        result = select(worked().assign(DD=0.0), config())
        self.assertEqual(result.run["signs"]["DD"], "positive")


class ConfigErrors(unittest.TestCase):
    def check(self, cfg, code):
        with self.assertRaises(ConfigError) as caught:
            load(cfg)
        self.assertTrue(any(p.startswith(code + " ") for p in caught.exception.problems),
                        f"{code} not in {caught.exception.problems}")

    def mutate(self, fn):
        cfg = copy.deepcopy(config())
        fn(cfg)
        return cfg

    def test_valid_config_loads(self):
        cfg = load(config(constraints=[{"metric": "DD", "op": "<=", "value": 20}]))
        self.assertEqual(cfg.id_column, "Variant")
        self.assertEqual(cfg.metric("DD").eps, 0.0)

    def test_c1(self):
        self.check(self.mutate(lambda c: c.pop("selection")), "C1")
        for n in (0, -1, 1.5, True, "3", None):
            self.check(self.mutate(lambda c: c["selection"].update(n=n)), "C1")

    def test_c2(self):
        self.check(self.mutate(lambda c: c["selection"].update(id_column="")), "C2")

    def test_c3(self):
        self.check(self.mutate(lambda c: c.update(metric=[])), "C3")

    def test_c4(self):
        self.check(self.mutate(lambda c: c.update(weights={})), "C4")
        self.check(self.mutate(lambda c: c["selection"].update(top=3)), "C4")
        self.check(self.mutate(lambda c: c["metric"][0].update(direciton="max")), "C4")
        self.check(config(constraints=[{"metric": "DD", "op": "<", "value": 1, "x": 1}]), "C4")

    def test_c5(self):
        self.check(self.mutate(lambda c: c["metric"][0].update(name="")), "C5")
        self.check(self.mutate(lambda c: c["metric"][1].update(name="Return")), "C5")

    def test_c6(self):
        self.check(self.mutate(lambda c: c["metric"][0].update(direction="up")), "C6")
        self.check(self.mutate(lambda c: c["metric"][0].pop("direction")), "C6")

    def test_c7(self):
        self.check(self.mutate(lambda c: c["metric"][0].update(role="both")), "C7")
        self.check(self.mutate(lambda c: [m.update(role="constraint_only") for m in c["metric"]]), "C7")

    def test_c8(self):
        self.check(self.mutate(lambda c: c["metric"][1].update(sign="neg")), "C8")

    def test_c9(self):
        self.check(self.mutate(lambda c: c["metric"][1].update(direction="max")), "C9")

    def test_c10(self):
        for eps in (-0.1, math.nan, math.inf, "0", True):
            self.check(self.mutate(lambda c: c["metric"][0].update(eps=eps)), "C10")

    def test_c11(self):
        self.check(self.mutate(lambda c: c["metric"][0].update(group="")), "C11")
        self.check(self.mutate(lambda c: c["metric"][0].update(group="g", role="constraint_only")),
                   "C11")

    def test_c12(self):
        self.check(self.mutate(lambda c: c["selection"].update(id_column="Return")), "C12")

    def test_c13(self):
        self.check(self.mutate(lambda c: c["metric"][2].update(requires_positive="Return")), "C13")
        self.check(self.mutate(lambda c: c["metric"][2].update(requires_positive="Sharpe")), "C13")
        self.check(self.mutate(lambda c: c["metric"][2].update(requires_positive="Nope")), "C13")
        self.check(self.mutate(lambda c: c["metric"][2].update(requires_positive="DD")), "C13")
        weak = self.mutate(lambda c: c["metric"][2].update(requires_positive="Return"))
        weak["constraint"] = [{"metric": "Return", "op": ">=", "value": 0}]
        self.check(weak, "C13")
        ok = self.mutate(lambda c: c["metric"][2].update(requires_positive="Return"))
        ok["constraint"] = [{"metric": "Return", "op": ">", "value": 0}]
        load(ok)

    def test_c14(self):
        self.check(config(constraints=[{"metric": "Nope", "op": "<", "value": 1}]), "C14")

    def test_c15(self):
        self.check(config(constraints=[{"metric": "DD", "op": "==", "value": 1}]), "C15")

    def test_c16(self):
        for value in (math.nan, math.inf, "1", None):
            self.check(config(constraints=[{"metric": "DD", "op": "<", "value": value}]), "C16")

    def test_c17(self):
        self.check(config(constraints=[{"metric": "DD", "op": "<=", "value": -5}]), "C17")

    def test_c18(self):
        self.check(config(constraints=[{"metric": "Return", "op": "<=", "value": 10},
                                       {"metric": "Return", "op": ">", "value": 20}]), "C18")
        self.check(config(constraints=[{"metric": "Return", "op": "<", "value": 10},
                                       {"metric": "Return", "op": ">=", "value": 10}]), "C18")
        self.check(config(constraints=[{"metric": "DD", "op": "<", "value": 0}]), "C18")
        load(config(constraints=[{"metric": "Return", "op": "<=", "value": 10},
                                 {"metric": "Return", "op": ">=", "value": 10}]))

    def test_all_problems_reported_together(self):
        cfg = self.mutate(lambda c: (c["selection"].update(n=0), c["metric"][0].update(direction="x")))
        with self.assertRaises(ConfigError) as caught:
            load(cfg)
        codes = {p.split()[0] for p in caught.exception.problems}
        self.assertTrue({"C1", "C6"} <= codes)


if __name__ == "__main__":
    unittest.main()
