"""The selection pipeline: config + table -> selection, audit and run info.

Stages, in order (DESIGN.md "In short"): validate config, validate data,
canonical form, hard constraints, Pareto fronts, ideal/nadir, gaps, score, pick.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import pareto, spec, stages

STATUS_ORDER = {"selected": 0, "not_selected": 0, "dominated": 0, "infeasible": 1, "invalid": 2}


@dataclass
class Selection:
    selected: list                  # original variant IDs, best first
    audit: pd.DataFrame             # one row per input variant
    run: dict = field(default_factory=dict)


def select(table: pd.DataFrame, config) -> Selection:
    """Pick the top n variants of `table` under `config` (a TOML path, a dict or a spec.Config)."""
    cfg = spec.load(config)
    ids, values, invalid, signs = stages.validate(table, cfg)
    original = dict(zip(values.index, ids))
    objectives = cfg.objectives
    weights = cfg.weights()
    warnings: list[str] = []
    run = {
        "signs": signs,
        "eps": {m.name: m.eps for m in objectives},
        "weights": weights,
        "ideal": None,
        "nadir": None,
        "range_fallbacks": [],
        "warnings": warnings,
        "diagnostic": None,
    }

    rows: dict[str, dict] = {key: {"status": "invalid", "reason": reason}
                             for key, reason in invalid.items()}
    valid = values.drop(index=list(invalid))
    if valid.empty:
        warnings.append("D11 every variant was rejected for NaN/inf; nothing to select")
        failed, diagnostic = {}, []
    else:
        failed, diagnostic = stages.constraints(valid, cfg)
    for key, broken in failed.items():
        rows[key] = {"status": "infeasible", "reason": "; ".join(broken)}
    feasible = valid.drop(index=list(failed))
    if not valid.empty and feasible.empty:
        run["diagnostic"] = diagnostic
        warnings.append("no variant satisfies the constraints; see run['diagnostic']")

    selected = []
    if not feasible.empty:
        selected = _rank(feasible, cfg, objectives, weights, original, rows, run)

    run["counts"] = {
        "input": len(values),
        "invalid": len(invalid),
        "infeasible": len(failed),
        "feasible": len(feasible),
        "front_1": sum(1 for r in rows.values() if r.get("front") == 1),
        "selected": len(selected),
    }
    return Selection(selected=selected, audit=_audit(values.index, rows, original, cfg, objectives),
                     run=run)


def _rank(feasible, cfg, objectives, weights, original, rows, run) -> list:
    warnings = run["warnings"]
    keys = feasible.index.to_numpy()
    u = np.column_stack([stages.canonical(m, feasible[m.name].to_numpy()) for m in objectives])
    eps = np.array([m.eps for m in objectives])
    front, dominator = pareto.fronts(u, eps)

    first = front == 1
    ideal, nadir = u[first].max(axis=0), u[first].min(axis=0)
    span = ideal - nadir
    for i, m in enumerate(objectives):
        if span[i] > 0:
            continue
        wide = u[:, i].max() - u[:, i].min()
        if wide > 0:
            span[i] = wide
            run["range_fallbacks"].append(m.name)
            warnings.append(f"{m.name!r} has zero range on front 1; using its range over all "
                            "feasible variants")
        else:
            warnings.append(f"{m.name!r} is identical for every feasible variant; its gaps are 0")
    gaps = np.divide(ideal - u, span, out=np.zeros_like(u), where=span > 0)
    w = np.array([weights[m.name] for m in objectives])
    score = gaps @ w

    # Rows are in ID order, so the row index breaks exact ties by ID.
    order = np.lexsort((np.arange(len(keys)), np.round(score, 12), front))
    rank = np.empty(len(keys), dtype=int)
    rank[order] = np.arange(1, len(keys) + 1)
    chosen = set(order[:cfg.n])

    if len(keys) == 1:
        warnings.append("only one feasible variant")
    if len(keys) < cfg.n:
        warnings.append(f"n = {cfg.n} but only {len(keys)} feasible variant(s); selecting all")

    # Report ideal and nadir as natural values: u for max metrics, -u for min metrics.
    to_natural = np.array([1.0 if m.direction == "max" else -1.0 for m in objectives])
    run["ideal"] = dict(zip((m.name for m in objectives), (ideal * to_natural).tolist()))
    run["nadir"] = dict(zip((m.name for m in objectives), (nadir * to_natural).tolist()))

    for i, key in enumerate(keys):
        beaten = f"dominated by {original[keys[dominator[i]]]}" if dominator[i] >= 0 else ""
        if i in chosen:
            status = "selected"
        elif front[i] == 1:
            status = "not_selected"
        else:
            status = "dominated"
        row = {"status": status, "reason": beaten, "front": int(front[i]),
               "score": float(score[i]), "rank": int(rank[i])}
        row.update({f"gap_{m.name}": float(gaps[i, j]) for j, m in enumerate(objectives)})
        rows[key] = row
    return [original[keys[i]] for i in order[:cfg.n]]


def _audit(index, rows, original, cfg, objectives) -> pd.DataFrame:
    gap_columns = [f"gap_{m.name}" for m in objectives]
    columns = [cfg.id_column, "status", "reason", "front", *gap_columns, "score", "rank"]
    records = []
    for position, key in enumerate(index):
        row = rows[key]
        records.append({cfg.id_column: original[key], "status": row["status"],
                        "reason": row.get("reason", ""), "front": row.get("front"),
                        **{c: row.get(c, np.nan) for c in gap_columns},
                        "score": row.get("score", np.nan), "rank": row.get("rank"),
                        "_group": STATUS_ORDER[row["status"]], "_position": position})
    audit = pd.DataFrame.from_records(records)
    audit["front"] = audit["front"].astype("Int64")
    audit["rank"] = audit["rank"].astype("Int64")
    audit = audit.sort_values(["_group", "rank", "_position"], na_position="last", kind="stable")
    return audit[columns].reset_index(drop=True)
