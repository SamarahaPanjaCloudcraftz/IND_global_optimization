"""Planning and launching, kept out of the Streamlit file so it can be tested.

Nothing here knows about tradelib. Launching goes through a `run_one` the user
names by import path, which is the only piece that touches the backtest engine.
"""

import ast
import importlib
import itertools
import json
import os
import pickle
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import (  # noqa: E402
    Job, PlanSize, branches, build_jobs, config_hash, job_dir, plan_size, run_all, sweep,
)

branches_of = branches

PLAN_FILE = "plan.json"        # the readable record
PLAN_PICKLE = "plan.pickle"    # what the detached runner reads back exactly
STATUS_FILE = "status.json"       # per stage: every job, its outcome, its attempts
RUN_FILE = "run.json"             # per run: is it finished, and how far did it get
RUNNER_LOG = "run_plan.log"

# A job that has failed this many times is settled and never retried, including
# on a resume. One retry covers a crash mid-job; a config the engine genuinely
# rejects would only burn the same time again.
MAX_ATTEMPTS = 2


# ---------------------------------------------------------------- values

def parse_value(line: str, like):
    """Read one typed value, guided by the type of the baseline value."""
    if isinstance(like, datetime):  # before date: datetime subclasses date
        return datetime.fromisoformat(line)
    if isinstance(like, time):
        return time.fromisoformat(line)
    if isinstance(like, date):
        return date.fromisoformat(line)
    try:
        return ast.literal_eval(line)
    except (ValueError, SyntaxError):
        pass
    if isinstance(like, (dict, list, tuple)):
        # A container of times or dates is not a literal, so read it with only
        # the datetime constructors in scope and nothing else.
        try:
            return eval(line, {"__builtins__": {}}, dict(_CONSTRUCTORS))  # noqa: S307
        except Exception:
            raise ValueError(f"could not read {line!r} as a {type(like).__name__}") from None
    if isinstance(like, str):
        return line  # bare identifiers, e.g. gamma_iv
    raise ValueError(f"could not read {line!r} as a {type(like).__name__}") from None


def parse_values(text: str, like) -> list:
    """One value per line. Newline-separated so dicts and lists stay readable."""
    return [parse_value(l.strip(), like) for l in text.splitlines() if l.strip()]


# Only what a config value may legitimately be built from, so a typed container
# can be read back without exposing anything else.
_CONSTRUCTORS = {"time": time, "date": date, "datetime": datetime, "timedelta": timedelta}


def spread(low, high, count: int) -> list:
    """`count` equally spaced values from `low` to `high`, both included.

    The unit of a sweep is almost always a range rather than a list of numbers
    someone typed, so this is what the dashboard's inputs produce. Times are
    interpolated through seconds-of-day and rounded to the minute; integers stay
    integers so a count of strikes never becomes 4.666.
    """
    count = max(int(count), 1)
    if isinstance(low, time) or isinstance(high, time):
        start, stop = _seconds(low), _seconds(high)
        steps = _steps(start, stop, count)
        return [_to_time(round(value / 60) * 60) for value in steps]
    steps = _steps(low, high, count)
    if isinstance(low, int) and isinstance(high, int) and not isinstance(low, bool):
        return list(dict.fromkeys(int(round(value)) for value in steps))
    return [round(value, 10) for value in steps]


def _steps(low, high, count: int) -> list[float]:
    if count == 1 or low == high:
        return [float(low)]
    gap = (float(high) - float(low)) / (count - 1)
    return [float(low) + gap * index for index in range(count)]


def _seconds(value: time) -> int:
    return value.hour * 3600 + value.minute * 60 + value.second


def _to_time(seconds: float) -> time:
    seconds = int(max(0, min(86340, seconds)))
    return time(seconds // 3600, (seconds % 3600) // 60, seconds % 60)


def render_value(value) -> str:
    """The inverse of parse_value, for prefilling the editor.

    Containers are rendered so they can be read back: a bare `repr` gives
    `datetime.time(9, 17)`, which no literal parser accepts, so times and dates
    inside a dict or list are written as the constructor calls parse_value
    understands.
    """
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return _render_inner(value)


def _render_inner(value) -> str:
    """Render a value as it appears inside a container."""
    if isinstance(value, datetime):
        return (f"datetime({value.year}, {value.month}, {value.day}, "
                f"{value.hour}, {value.minute}, {value.second})")
    if isinstance(value, date):
        return f"date({value.year}, {value.month}, {value.day})"
    if isinstance(value, time):
        return f"time({value.hour}, {value.minute}, {value.second})"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_render_inner(k)}: {_render_inner(v)}"
                               for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_render_inner(v) for v in value) + "]"
    if isinstance(value, tuple):
        inner = ", ".join(_render_inner(v) for v in value)
        return f"({inner},)" if len(value) == 1 else f"({inner})"
    return repr(value)


def render_values(values) -> str:
    return "\n".join(render_value(v) for v in values)


WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def key_label(key) -> str:
    """Name a dict-valued parameter's key, as a weekday where that is what it is."""
    if isinstance(key, int) and not isinstance(key, bool) and 0 <= key <= 6:
        return WEEKDAYS[key]
    return str(key)


def weekday_index(label: str) -> int | None:
    """The weekday a node name refers to, or None if it is not a weekday."""
    return WEEKDAYS.index(label) if label in WEEKDAYS[:5] else None


def dict_product(per_key: dict) -> list[dict]:
    """Every combination of per-key value lists, as whole dicts.

    A parameter like the per-weekday hedge constant is one config value, but it
    is natural to choose a range for each weekday. This expands those ranges the
    way the hand-written driver's nested loops did.
    """
    keys = list(per_key)
    return [
        dict(zip(keys, combination))
        for combination in itertools.product(*(per_key[key] for key in keys))
    ]


def _json_default(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return repr(value)


# ---------------------------------------------------------------- plan

@dataclass
class Plan:
    strategy: str
    runner: str
    groups: dict
    run: str
    stage: str
    baseline: dict
    only: list[str]
    sweeps: dict
    jobs: list[Job]
    size: PlanSize

    def stage_dir(self) -> Path:
        return Path(self.run) / self.stage

    def dir_of(self, job: Job) -> Path:
        return job_dir(job, self.run, self.stage)

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "runner": self.runner,
            # Which group each axis belongs to. Recorded here so an analysis of
            # this run needs the run directory and nothing else - not the
            # strategy module, which may have been renamed or changed since.
            "groups": self.groups,
            "run": self.run,
            "stage": self.stage,
            "created": datetime.now().isoformat(timespec="seconds"),
            "axes": self.only,
            "sweeps": self.sweeps,
            "baseline": self.baseline,
            "size": {"variants": self.size.variants, "runs": self.size.runs,
                     "deduped": self.size.deduped},
            "jobs": [
                {"axis": job.axis, "hash": config_hash(job.config),
                 "dir": str(self.dir_of(job)), "config": job.config}
                for job in self.jobs
            ],
        }


def build_plan(strategy: str, tree, baseline: dict, sweeps: dict, only: list[str],
               run: str, stage: str, runner: str = "", groups: dict | None = None) -> Plan:
    """Apply the user's selection to the strategy's tree and lay out the jobs."""
    chosen = sweep(tree, sweeps)
    return Plan(
        strategy=strategy, runner=runner, groups=dict(groups or {}),
        run=run, stage=stage, baseline=baseline,
        only=only, sweeps=sweeps,
        jobs=build_jobs(chosen, baseline, only),
        size=plan_size(chosen, baseline, only),
    )


def write_plan(plan: Plan) -> Path:
    """Record the plan under the stage directory. Returns where the JSON landed.

    Two files: `plan.json` to read, and `plan.pickle` for the detached runner to
    load back. JSON cannot round-trip these configs - it has no integer dict
    keys and no dates - so it is the record, not the source of truth.
    """
    path = plan.stage_dir() / PLAN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan.as_dict(), indent=2, default=_json_default))
    (plan.stage_dir() / PLAN_PICKLE).write_bytes(pickle.dumps(plan))
    return path


def read_plan(pickle_path) -> Plan:
    """Load a plan exactly as it was built."""
    return pickle.loads(Path(pickle_path).read_bytes())


def is_running(pid) -> bool:
    """Whether a runner process is still alive.

    Without this, a stage that crashed looks identical to one still working:
    both are simply unfinished.
    """
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


def read_run(run) -> dict | None:
    """The run-level record: whether it finished and how far each stage got."""
    path = Path(run) / RUN_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def read_status(stage_dir) -> dict | None:
    """The status of a stage, or None if it has not started.

    Returns None rather than raising on a torn read, though `launch` writes
    atomically so that should not happen.
    """
    path = Path(stage_dir) / STATUS_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def unfinished_run(outputs_root=None):
    """The most recently touched stage that has not finished, as (run, stage).

    Lets a restarted dashboard land back on a sweep that is still going, instead
    of a fresh empty run directory.
    """
    root = Path(outputs_root or (ROOT / "outputs"))
    if not root.exists():
        return None
    stages = sorted(root.glob("*/*/" + STATUS_FILE),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    for status_path in stages:
        status = read_status(status_path.parent)
        if status and status["finished"] < status["total"]:
            return str(status_path.parent.parent), status_path.parent.name
    return None


def start_detached(plan: Plan, resume: bool = False) -> int:
    """Run the sweep in its own session; return the runner's pid.

    The sweep must outlive the dashboard. Streamlit reruns its script on every
    widget interaction, which would kill an in-process loop at its next `st.*`
    call - so a click, a restart or a closed tab would each end a sweep midway.
    """
    if not resume:
        write_plan(plan)
    script = Path(__file__).resolve().parent / "run_plan.py"
    log = (plan.stage_dir() / RUNNER_LOG).open("w")
    process = subprocess.Popen(
        [sys.executable, str(script), str(plan.stage_dir() / PLAN_PICKLE)]
        + (["--resume"] if resume else []),
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return process.pid


# ---------------------------------------------------------------- launch

def load_run_one(spec: str):
    """Import the caller's run_one, given 'module.path:attribute'."""
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise ValueError("expected 'module.path:attribute', e.g. 'my_runner:run_one'")
    function = getattr(importlib.import_module(module_name), attr)
    if not callable(function):
        raise TypeError(f"{spec} is not callable")
    return function


def _write_atomic(path: Path, text: str) -> None:
    """Replace a file in one step, so a reader never sees it half-written."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def launch(plan: Plan, run_one, resume: bool = False):
    """Run the stage's jobs, yielding (index, job, ok, detail) as each finishes.

    On a resume a job is skipped when it has already returned cleanly, or when
    it has failed MAX_ATTEMPTS times and is settled. Everything else runs again,
    including a job that was mid-flight when the process died — which leaves no
    status entry at all.

    A job about to run has its directory removed first. A backtest killed part
    way leaves some days written and no result.json, and the combiner would
    otherwise chain those stale days into the new run.

    Status is rewritten after every attempt, so a crash always leaves an
    accurate record of how far the sweep got.
    """
    status_path = plan.stage_dir() / STATUS_FILE
    status_path.parent.mkdir(parents=True, exist_ok=True)

    existing = (read_status(plan.stage_dir()) or {}) if resume else {}
    record = {entry["hash"]: entry for entry in existing.get("jobs", [])}
    started = existing.get("started") or datetime.now().isoformat(timespec="seconds")

    def settled(entry: dict) -> bool:
        return entry["ok"] or entry.get("attempts", 1) >= MAX_ATTEMPTS

    def save() -> None:
        jobs = [record[config_hash(job.config)] for job in plan.jobs
                if config_hash(job.config) in record]
        finished = sum(1 for entry in jobs if settled(entry))
        failed = sum(1 for entry in jobs if not entry["ok"] and settled(entry))
        _write_atomic(status_path, json.dumps({
            "plan": str(plan.stage_dir() / PLAN_FILE),
            "pid": os.getpid(), "started": started,
            "updated": datetime.now().isoformat(timespec="seconds"),
            "total": len(plan.jobs), "finished": finished, "failed": failed,
            "jobs": jobs,
        }, indent=2, default=_json_default))
        _save_run(plan, total=len(plan.jobs), finished=finished, failed=failed)

    save()
    index = 0
    for job in plan.jobs:
        digest = config_hash(job.config)
        entry = record.get(digest)
        if entry and settled(entry):
            continue

        attempts = (entry or {}).get("attempts", 0)
        while attempts < MAX_ATTEMPTS:
            directory = plan.dir_of(job)
            if directory.exists():
                shutil.rmtree(directory)
            mark = datetime.now()
            try:
                detail, ok = run_one(job, directory), True
            except Exception as error:  # noqa: BLE001 - recorded, not swallowed
                detail, ok = f"{type(error).__name__}: {error}", False
            attempts += 1
            record[digest] = {
                "hash": digest, "axis": job.axis, "ok": ok, "attempts": attempts,
                "detail": str(detail),
                "seconds": round((datetime.now() - mark).total_seconds(), 1),
            }
            save()
            index += 1
            yield index, job, ok, detail
            if ok:
                break


def _save_run(plan: Plan, *, total: int, finished: int, failed: int) -> None:
    """Update the run-level record beside the dated run directory.

    Written independently of any stage's status file, so a resume can check one
    against the other before trusting either.
    """
    path = Path(plan.run) / RUN_FILE
    state = read_run(plan.run) or {}
    state.setdefault("run", str(plan.run))
    state.setdefault("started", datetime.now().isoformat(timespec="seconds"))
    state["updated"] = datetime.now().isoformat(timespec="seconds")
    stages = state.setdefault("stages", {})
    stages[plan.stage] = {
        "total": total, "finished": finished, "failed": failed,
        "complete": finished >= total, "pid": os.getpid(),
    }
    state["complete"] = all(stage["complete"] for stage in stages.values())
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, json.dumps(state, indent=2))
