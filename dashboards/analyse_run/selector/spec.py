"""Selector config: parsing and validation (DESIGN.md §4, §5).

A config comes from a TOML file or an equivalent dict. Every problem found is
collected and raised together as one ConfigError, so a broken config is fixed in
one pass rather than one error at a time. Nothing here looks at the data.
"""

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

DIRECTIONS = ("max", "min")
SIGNS = ("none", "negative", "positive", "auto")
ROLES = ("objective", "constraint_only")
OPS = ("<=", "<", ">=", ">")

SECTION_KEYS = {"selection", "metric", "constraint"}
SELECTION_KEYS = {"n", "id_column"}
METRIC_KEYS = {"name", "direction", "role", "sign", "eps", "group", "requires_positive"}
CONSTRAINT_KEYS = {"metric", "op", "value"}


class ConfigError(ValueError):
    """The config is invalid. `problems` lists every check that failed."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("invalid selector config:\n  - " + "\n  - ".join(problems))


@dataclass(frozen=True)
class Metric:
    name: str
    direction: str
    role: str = "objective"
    sign: str = "none"
    eps: float = 0.0
    group: str | None = None
    requires_positive: str | None = None

    @property
    def objective(self) -> bool:
        return self.role == "objective"

    @property
    def magnitude(self) -> bool:
        """True when the metric is judged on |x| (it carries a sign convention)."""
        return self.sign != "none"


@dataclass(frozen=True)
class Constraint:
    metric: str
    op: str
    value: float

    def __str__(self) -> str:
        return f"{self.metric} {self.op} {self.value:g}"


@dataclass(frozen=True)
class Config:
    n: int
    id_column: str
    metrics: tuple[Metric, ...]
    constraints: tuple[Constraint, ...]

    @property
    def objectives(self) -> tuple[Metric, ...]:
        return tuple(m for m in self.metrics if m.objective)

    def metric(self, name: str) -> Metric:
        return next(m for m in self.metrics if m.name == name)

    def weights(self) -> dict[str, float]:
        """Equal weights summing to 1; a group shares one unit among its members."""
        group_size: dict[str, int] = {}
        for m in self.objectives:
            if m.group is not None:
                group_size[m.group] = group_size.get(m.group, 0) + 1
        units = {m.name: 1.0 / group_size[m.group] if m.group is not None else 1.0
                 for m in self.objectives}
        total = sum(units.values())
        return {name: unit / total for name, unit in units.items()}


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite_number(value) -> bool:
    return _is_number(value) and math.isfinite(value)


def load(source) -> Config:
    """Read and validate a config from a TOML path or a dict."""
    if isinstance(source, Config):
        return source
    if isinstance(source, (str, Path)):
        with open(source, "rb") as f:
            raw = tomllib.load(f)
    elif isinstance(source, dict):
        raw = source
    else:
        raise ConfigError([f"config must be a TOML path or a dict, got {type(source).__name__}"])
    return _parse(raw)


def _parse(raw: dict) -> Config:
    problems: list[str] = []

    unknown = set(raw) - SECTION_KEYS
    if unknown:
        problems.append(f"C4 unknown top-level key(s): {sorted(unknown)}")

    # [selection]
    selection = raw.get("selection")
    n, id_column = None, "Variant"
    if not isinstance(selection, dict):
        problems.append("C1 [selection] section missing")
    else:
        unknown = set(selection) - SELECTION_KEYS
        if unknown:
            problems.append(f"C4 unknown key(s) in [selection]: {sorted(unknown)}")
        n = selection.get("n")
        if not (isinstance(n, int) and not isinstance(n, bool) and n >= 1):
            problems.append(f"C1 selection.n must be an integer >= 1, got {n!r}")
        id_column = selection.get("id_column", "Variant")
        if not (isinstance(id_column, str) and id_column):
            problems.append(f"C2 selection.id_column must be a non-empty string, got {id_column!r}")

    # [[metric]]
    raw_metrics = raw.get("metric")
    metrics: list[Metric] = []
    if not isinstance(raw_metrics, list) or not raw_metrics:
        problems.append("C3 no [[metric]] blocks")
        raw_metrics = []
    seen: set[str] = set()
    for i, block in enumerate(raw_metrics):
        where = f"metric #{i + 1}"
        if not isinstance(block, dict):
            problems.append(f"C5 {where} is not a table")
            continue
        name = block.get("name")
        if isinstance(name, str) and name:
            where = f"metric {name!r}"
        unknown = set(block) - METRIC_KEYS
        if unknown:
            problems.append(f"C4 unknown key(s) in {where}: {sorted(unknown)}")
        ok = True
        if not (isinstance(name, str) and name):
            problems.append(f"C5 {where}: name must be a non-empty string, got {name!r}")
            ok = False
        elif name in seen:
            problems.append(f"C5 duplicate metric name {name!r}")
            ok = False
        else:
            seen.add(name)
        direction = block.get("direction")
        if direction not in DIRECTIONS:
            problems.append(f"C6 {where}: direction must be one of {list(DIRECTIONS)}, got {direction!r}")
            ok = False
        role = block.get("role", "objective")
        if role not in ROLES:
            problems.append(f"C7 {where}: role must be one of {list(ROLES)}, got {role!r}")
            ok = False
        sign = block.get("sign", "none")
        if sign not in SIGNS:
            problems.append(f"C8 {where}: sign must be one of {list(SIGNS)}, got {sign!r}")
            ok = False
        elif sign != "none" and direction == "max":
            problems.append(f"C9 {where}: sign {sign!r} needs direction = \"min\" "
                            "(a sign convention means: minimise the magnitude)")
            ok = False
        eps = block.get("eps", 0.0)
        if not (_finite_number(eps) and eps >= 0):
            problems.append(f"C10 {where}: eps must be a finite number >= 0, got {eps!r}")
            ok = False
        group = block.get("group")
        if group is not None:
            if not (isinstance(group, str) and group):
                problems.append(f"C11 {where}: group must be a non-empty string, got {group!r}")
                ok = False
            elif role == "constraint_only":
                problems.append(f"C11 {where}: group is set on a constraint_only metric")
                ok = False
        requires_positive = block.get("requires_positive")
        if requires_positive is not None and not (isinstance(requires_positive, str) and requires_positive):
            problems.append(f"C13 {where}: requires_positive must be a metric name, got {requires_positive!r}")
            ok = False
        if ok:
            metrics.append(Metric(name=name, direction=direction, role=role, sign=sign,
                                  eps=float(eps), group=group, requires_positive=requires_positive))

    by_name = {m.name: m for m in metrics}
    if raw_metrics and metrics and not any(m.objective for m in metrics):
        problems.append("C7 no metric has role = \"objective\"")
    if isinstance(id_column, str) and id_column in seen:
        problems.append(f"C12 id_column {id_column!r} is also a metric name")

    # [[constraint]]
    raw_constraints = raw.get("constraint", [])
    constraints: list[Constraint] = []
    if not isinstance(raw_constraints, list):
        problems.append("C14 [[constraint]] must be a list of tables")
        raw_constraints = []
    for i, block in enumerate(raw_constraints):
        where = f"constraint #{i + 1}"
        if not isinstance(block, dict):
            problems.append(f"C14 {where} is not a table")
            continue
        unknown = set(block) - CONSTRAINT_KEYS
        if unknown:
            problems.append(f"C4 unknown key(s) in {where}: {sorted(unknown)}")
        metric, op, value = block.get("metric"), block.get("op"), block.get("value")
        ok = True
        if metric not in seen:
            problems.append(f"C14 {where}: metric {metric!r} is not declared")
            ok = False
        if op not in OPS:
            problems.append(f"C15 {where}: op must be one of {list(OPS)}, got {op!r}")
            ok = False
        if not _finite_number(value):
            problems.append(f"C16 {where}: value must be a finite number, got {value!r}")
            ok = False
        elif metric in by_name and by_name[metric].magnitude and value < 0:
            problems.append(f"C17 {where}: {metric!r} is judged on its magnitude, "
                            f"so the threshold cannot be negative ({value!r})")
            ok = False
        if ok:
            constraints.append(Constraint(metric=metric, op=op, value=float(value)))

    problems += _unsatisfiable(constraints, by_name)
    problems += _requires_positive(metrics, constraints, by_name, seen)

    if problems:
        raise ConfigError(problems)
    return Config(n=n, id_column=id_column, metrics=tuple(metrics), constraints=tuple(constraints))


def _unsatisfiable(constraints: list[Constraint], by_name: dict[str, Metric]) -> list[str]:
    """C18: constraints on one metric whose intervals don't overlap."""
    problems = []
    for name in dict.fromkeys(c.metric for c in constraints):
        if name not in by_name:
            continue
        # (bound, inclusive); a magnitude can never be below 0
        lo = (0.0, True) if by_name[name].magnitude else (-math.inf, False)
        hi = (math.inf, False)
        for c in (c for c in constraints if c.metric == name):
            if c.op in (">", ">="):
                bound = (c.value, c.op == ">=")
                if bound[0] > lo[0] or (bound[0] == lo[0] and not bound[1]):
                    lo = bound
            else:
                bound = (c.value, c.op == "<=")
                if bound[0] < hi[0] or (bound[0] == hi[0] and not bound[1]):
                    hi = bound
        if lo[0] > hi[0] or (lo[0] == hi[0] and not (lo[1] and hi[1])):
            listed = ", ".join(str(c) for c in constraints if c.metric == name)
            problems.append(f"C18 no value of {name!r} satisfies all of: {listed}")
    return problems


def _requires_positive(metrics, constraints, by_name, declared) -> list[str]:
    """C13: a ratio metric's numerator must be guaranteed > 0 by a constraint."""
    problems = []
    for m in metrics:
        target = m.requires_positive
        if target is None:
            continue
        if target == m.name:
            problems.append(f"C13 metric {m.name!r}: requires_positive names itself")
        elif target not in declared:
            problems.append(f"C13 metric {m.name!r}: requires_positive names undeclared metric {target!r}")
        elif target in by_name and by_name[target].magnitude:
            problems.append(f"C13 metric {m.name!r}: requires_positive target {target!r} has a sign "
                            "convention, so a constraint on it bounds |x|, not x")
        elif not any(c.metric == target and ((c.op == ">" and c.value >= 0) or
                                             (c.op == ">=" and c.value > 0))
                     for c in constraints):
            problems.append(f"C13 metric {m.name!r} requires {target!r} > 0, but no constraint "
                            f"guarantees it (add: metric = {target!r}, op = \">\", value = 0)")
    return problems
