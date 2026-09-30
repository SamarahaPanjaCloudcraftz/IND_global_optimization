"""Composing axis winners into combination backtests.

Per weekday:

  hedge  H = delta winner + gamma winner            H* = best of {delta, gamma, H}
  sell   S = condor + signal-strength + trade-time  S* = best of {the three, S}
  final  F = H* + S*                                answer = best of {H*, S*, F}

Every candidate is the weekday's baseline config with some axes' own keys laid
over it. A key belongs to an axis when some job of that axis differs from the
control on it — which picks up pinned parameters (gamma_hedge = True) as well
as swept ones. The gamma axis sweeps percent_hedge, but the gamma hedge now
reads gamma_pct_hedge, so its value is carried across under that name.

Combinations are planned and run exactly like any sweep: a plan.json and
plan.pickle under <stage>/combinations/<round>/, executed by run_plan.py. A
candidate is only run when no existing backtest already does the same thing —
compared by the hash of its effective config (see `effective`).
"""

import json
import pickle
import sys
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _path in (ROOT, ROOT / "dashboards" / "plan_and_start"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import plan_io as P  # noqa: E402
from engine import Job, PlanSize, config_hash, job_dir  # noqa: E402

COMBINATIONS = "combinations"
REGISTRY = "registry.pickle"
RUNNER = "tradelib_runner:run_one"
SELECTOR = "day_of_week_signal_strength"
GAMMA_AXIS = "gamma_hedging"
RENAMED = {(GAMMA_AXIS, "percent_hedge"): "gamma_pct_hedge"}

HEDGE_AXES = ["delta_hedging", "gamma_hedging"]
SELL_AXES = ["condor_OTM_outstrike", "dow_signal_strength", "trade_time"]
ROUNDS = {"round1": "hedge and sell combinations", "round2": "final combinations"}


# ------------------------------------------------------------ native configs

def _like(value, model):
    """A JSON value converted back to the native type `model` has."""
    if isinstance(model, bool) or model is None:
        return value
    if isinstance(model, time):
        return time.fromisoformat(value)
    if isinstance(model, date):
        return date.fromisoformat(value)
    if isinstance(model, dict):
        int_keys = bool(model) and isinstance(next(iter(model)), int)
        sample = next(iter(model.values())) if model else None
        return {(int(k) if int_keys else k): _like(v, model.get(int(k) if int_keys else k, sample))
                for k, v in value.items()}
    if isinstance(model, (list, tuple)):
        return type(model)(_like(v, model[0] if model else v) for v in value)
    if isinstance(model, float) and isinstance(value, int):
        return float(value)
    return value


def native_configs(stage_dir: Path) -> dict[str, dict]:
    """hash -> the exact native config of every job the stage's plan records.

    Taken from the stage's plan.pickle where it has the job. Jobs merged into
    plan.json afterwards are rebuilt from their JSON record, typed after the
    native control config — and kept only if the rebuilt config hashes back to
    the recorded hash, so a rebuild can never stand in for the wrong job.
    """
    stage = json.loads((stage_dir / P.PLAN_FILE).read_text())
    pickled = stage_dir / P.PLAN_PICKLE
    native = {}
    if pickled.exists():
        native = {config_hash(job.config): job.config for job in P.read_plan(pickled).jobs}
    template = next((native[e["hash"]] for e in stage["jobs"]
                     if e["axis"] is None and e["hash"] in native), None)
    out = {}
    for entry in stage["jobs"]:
        if entry["hash"] in native:
            out[entry["hash"]] = native[entry["hash"]]
        elif template is not None:
            rebuilt = {k: _like(v, template.get(k, v)) for k, v in entry["config"].items()}
            if config_hash(rebuilt) == entry["hash"]:
                out[entry["hash"]] = rebuilt
    return out


def as_json(config: dict) -> dict:
    """The config as plan.json records it — string keys, ISO times."""
    return json.loads(json.dumps(config, default=P._json_default))


# ------------------------------------------------------------ equivalence

def effective(config: dict) -> dict:
    """The config reduced to what the engine does with it.

    The delta hedge uses percent_hedge only when custom_pct_to_hedge is on and
    hedges 100% otherwise; the gamma hedge's fraction matters only when it is
    on, and stage-one jobs, which predate gamma_pct_hedge, used percent_hedge.
    """
    out = dict(config)
    gamma_pct = out.pop("gamma_pct_hedge", config.get("percent_hedge"))
    delta_pct = config.get("percent_hedge") if config.get("custom_pct_to_hedge") else 1.0
    out["custom_pct_to_hedge"], out["percent_hedge"] = True, delta_pct
    if config.get("gamma_hedge"):
        out["gamma_pct_hedge"] = gamma_pct
    return out


def effective_hash(config: dict) -> str:
    return config_hash(effective(config))


# ------------------------------------------------------------ composition

def owned_keys(stage_dir: Path) -> dict[str, set[str]]:
    """axis -> the config keys its jobs set away from the control."""
    jobs = json.loads((stage_dir / P.PLAN_FILE).read_text())["jobs"]
    control = next(e["config"] for e in jobs if e["axis"] is None)
    owned: dict[str, set[str]] = {}
    for entry in jobs:
        if entry["axis"] is None:
            continue
        root = entry["axis"].split("/")[0]
        keys = owned.setdefault(root, set())
        keys.update(k for k, v in entry["config"].items()
                    if k != SELECTOR and json.dumps(v, sort_keys=True) != json.dumps(control.get(k), sort_keys=True))
    return owned


def contribution(root: str, config: dict, owned: dict[str, set[str]]) -> dict:
    """What an axis winner lays over the baseline: its axis's own keys."""
    return {RENAMED.get((root, key), key): config[key] for key in owned.get(root, ()) if key in config}


def compose(base: dict, contrib: dict) -> dict:
    """The baseline with a contribution laid over it. gamma_pct_hedge is always
    written, so no run depends on a value an earlier run left in the engine."""
    config = {**base, **contrib}
    config.setdefault("gamma_pct_hedge", config["percent_hedge"])
    return config


@dataclass
class Candidate:
    """One entry in a best-of comparison."""

    name: str            # e.g. "delta_hedging winner", "H", "baseline"
    label: str           # what it is, for display
    contrib: dict        # the keys it lays over the baseline
    config: dict         # the complete native config it runs
    parts: list[str] = field(default_factory=list)   # the candidates it combines


# ------------------------------------------------------------ results index

def registry(stage_dir: Path) -> list[dict]:
    """Every combination job ever planned for this stage: config and directory."""
    path = stage_dir / COMBINATIONS / REGISTRY
    return pickle.loads(path.read_bytes()) if path.exists() else []


def results_index(stage_dir: Path, natives: dict[str, dict]) -> dict[str, Path]:
    """effective hash -> the directory of a finished backtest doing that.

    Stage-one jobs are found by their plan hash; combination jobs through the
    registry. Only directories holding a result.json count.
    """
    finished = {result.parent.name: result.parent for result in stage_dir.rglob("result.json")
                if COMBINATIONS not in result.relative_to(stage_dir).parts}
    index = {}
    for digest, config in natives.items():
        if digest in finished:
            index.setdefault(effective_hash(config), finished[digest])
    for entry in registry(stage_dir):
        directory = Path(entry["dir"])
        if (directory / "result.json").exists():
            index.setdefault(effective_hash(entry["config"]), directory)
    return index


# ------------------------------------------------------------ planning

def round_dir(stage_dir: Path, name: str) -> Path:
    return stage_dir / COMBINATIONS / name


def write_round(stage_dir: Path, name: str, strategy: str,
                wanted: list[tuple[str, dict]]) -> Path:
    """Write a round's plan for (axis tag, config) pairs and register its jobs.

    The axis tag, e.g. "Monday/hedge", becomes the directories under the round,
    so each job lands at <stage>/combinations/<round>/<Weekday>/<kind>/<hash>.
    """
    jobs = [Job(config=config, axis=tag) for tag, config in wanted]
    run = str(stage_dir / COMBINATIONS)
    plan = P.Plan(strategy=strategy, runner=RUNNER, groups={}, run=run, stage=name,
                  baseline={}, only=[], sweeps={}, jobs=jobs,
                  size=PlanSize(variants=len(jobs), runs=len(jobs), deduped=0))
    path = P.write_plan(plan)
    known = registry(stage_dir)
    seen = {config_hash(e["config"]) for e in known}
    known += [{"config": job.config, "dir": str(job_dir(job, run, name))}
              for job in jobs if config_hash(job.config) not in seen]
    (stage_dir / COMBINATIONS / REGISTRY).write_bytes(pickle.dumps(known))
    return path.parent / P.PLAN_PICKLE


def round_status(stage_dir: Path, name: str) -> dict | None:
    return P.read_status(round_dir(stage_dir, name))


def command(pickle_path: Path) -> str:
    return (f"cd {ROOT} && ./gopt_env/bin/python dashboards/plan_and_start/run_plan.py "
            f"{pickle_path} --resume")
