"""Pareto dominance and non-dominated sorting (DESIGN.md §7).

Works on canonical values, where every objective is "higher is better", so
dominance is plain >= / > comparisons. A dominates B iff A >= B on every
objective and A > B + eps on at least one. eps only loosens the second
condition, so the relation stays a strict partial order: no cycles, and the
first front is never empty.
"""

import numpy as np


def dominance(u: np.ndarray, eps: np.ndarray) -> np.ndarray:
    """dom[i, j] is True when row i dominates row j. u is (variants, objectives)."""
    at_least = (u[:, None, :] >= u[None, :, :]).all(axis=2)
    clearly = (u[:, None, :] > u[None, :, :] + eps).any(axis=2)
    return at_least & clearly


def fronts(u: np.ndarray, eps: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Front number per row (1 = non-dominated) and, per row, the index of one
    dominator (-1 on front 1): the one on the lowest front, ties to the lowest
    row index. Rows are expected in ID order, so "lowest index" means lowest ID."""
    dom = dominance(u, eps)
    count = len(u)
    front = np.zeros(count, dtype=int)
    remaining = np.ones(count, dtype=bool)
    level = 0
    while remaining.any():
        level += 1
        beaten = (dom & remaining[:, None]).any(axis=0)
        current = remaining & ~beaten
        front[current] = level
        remaining &= ~current

    dominator = np.full(count, -1, dtype=int)
    for j in np.flatnonzero(front > 1):
        rivals = np.flatnonzero(dom[:, j])
        dominator[j] = rivals[np.lexsort((rivals, front[rivals]))[0]]
    return front, dominator
