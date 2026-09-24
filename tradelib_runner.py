"""Running one tradelib backtest.

With `tradelib_format`, one of only two files that know this backtest engine
exists. Everything above them - tree, plan, selection, dashboard - never learns
what a backtest is.

The engine has no config API, so a run is set up by rewriting assignment lines
in `tradelib_global_constants.py` and then launching the pipeline against it.
Two consequences worth knowing:

  - That file is shared state. Runs must be sequential, and whatever the last
    job wrote is left behind when the sweep ends, exactly as the hand-written
    driver has always done.
  - The backtest engine runs on its own, older interpreter. This module runs
    under the optimization engine's environment and only launches that one.

Where the engine lives and what runs it come from `config.toml`, which is read
at import. A bad path fails here rather than part-way through a sweep, and the
dashboard surfaces it as a warning before anything can be started.

See `docs/Backtest_engine_constraints.md`.
"""

import os
import re
import subprocess
import tomllib
from pathlib import Path

from tradelib_format import as_assignments

CONFIG_FILE = Path(__file__).resolve().parent / "config.toml"


def _engine_paths() -> tuple[Path, Path]:
    """Read the backtest engine's location and interpreter from config.toml."""
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"missing {CONFIG_FILE}")
    with CONFIG_FILE.open("rb") as handle:
        section = tomllib.load(handle).get("backtest_engine", {})
    try:
        repo = Path(section["repo"]).expanduser()
        python = Path(section["python"]).expanduser()
    except KeyError as missing:
        raise KeyError(f"config.toml needs backtest_engine.{missing.args[0]}") from None
    if not python.is_absolute():
        python = repo / python
    for key, path in (("repo", repo), ("python", python)):
        if not path.exists():
            raise FileNotFoundError(f"config.toml: backtest_engine.{key} does not exist: {path}")
    return repo, python


REPO, PYTHON = _engine_paths()
CONSTANTS = REPO / "tradelib" / "tradelib_global_constants.py"
# Only what a sweep needs: the backtest, and the combiner that writes
# result.json. The chartbook and chartpack steps are deliberately excluded -
# they are presentation, they have a known failure, and a sweep of dozens of
# runs has no use for per-run chart packs.
PIPELINE = [
    "tradelib/main_backtest_single_process.py",
    "tradelib/backtest_combiner.py",
]


def write_config(assignments: dict[str, str], path: Path = CONSTANTS) -> None:
    """Rewrite each parameter's assignment line in the constants file.

    Raises if a parameter has no module-level assignment to rewrite. Silence
    there would be the worst possible failure: the sweep would run that job at
    the baseline value while labelling its output with the swept one.
    """
    source = path.read_text()
    missing = []
    for name, value in assignments.items():
        pattern = re.compile(rf"^{re.escape(name)}\s*=.*$", re.MULTILINE)
        source, found = pattern.subn(lambda _, n=name, v=value: f"{n} = {v}", source)
        if not found:
            missing.append(name)
    if missing:
        raise KeyError(f"no module-level assignment in {path.name} for: {sorted(missing)}")
    path.write_text(source)


def run_one(job, out_dir) -> Path:
    """Run one backtest and return where it landed.

    The pipeline's output is captured to `run.log` beside the results, so a
    failure in an overnight sweep leaves something to read.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_config(as_assignments({**job.config, "output_dir_folder": str(out_dir)}))

    log = out_dir / "run.log"
    environment = {**os.environ, "PYTHONPATH": str(REPO)}
    with log.open("w") as handle:
        for script in PIPELINE:
            handle.write(f"\n===== {script} =====\n")
            handle.flush()
            finished = subprocess.run(
                [str(PYTHON), script], cwd=REPO, env=environment,
                stdout=handle, stderr=subprocess.STDOUT,
            )
            if finished.returncode:
                raise RuntimeError(f"{script} exited {finished.returncode}; see {log}")
    return out_dir
