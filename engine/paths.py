"""Where a backtest job's output goes.

The layout mirrors the tree: one directory per independent axis, and a product
axis gets its own directory as a whole, never one nested per parameter - a
product's parameters are chosen jointly, and a nested path would invite reading
one level independently of the other.

    <run>/<stage>/<axis>/<config hash>/

The leaf directory is named by a digest of the *complete* config, not of the
partial that defines the variant. That makes the name globally unique and
stable: the same config lands in the same directory name in any axis, any
stage, any run, so results can be joined across them later.
"""

import hashlib
import re
from pathlib import Path

from .plan import Job, canonical

CONTROL_DIR = "control"


def config_hash(config: dict, length: int = 12) -> str:
    """A stable digest of a complete config.

    Deterministic across processes and runs, which Python's builtin hash() is
    not: string hashing is randomized per process unless PYTHONHASHSEED is set.
    Insensitive to the order a dict-valued parameter was written in.
    """
    return hashlib.sha256(repr(canonical(config)).encode()).hexdigest()[:length]


def job_dir(job: Job, run: str | Path, stage: str) -> Path:
    """The directory this job's backtest should write into.

    A branch path nests: "delta_hedging/gamma_iv/Monday" becomes three
    directories, so the layout mirrors the tree.
    """
    tag = job.axis or CONTROL_DIR
    segments = [re.sub(r"\s+", "_", part) for part in tag.split("/")]
    return Path(run).joinpath(stage, *segments, config_hash(job.config))
