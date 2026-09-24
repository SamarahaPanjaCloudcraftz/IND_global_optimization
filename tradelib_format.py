"""Turn native config values into source text for tradelib_global_constants.py.

The backtest engine has no config API. A run is set up by rewriting assignment
lines in that file and then launching, so every value has to arrive as valid
Python source that still means the same thing once the file is imported.

The file's imports are `os, json, numpy as np` and
`from datetime import timedelta, date, time, datetime`. That is what makes the
bare `time(9, 17, 0)` and `date(2026, 1, 1)` forms below the correct ones.

repr() is deliberately not used for those: it yields `datetime.time(9, 17)`,
and the file binds `datetime` to the class rather than the module, so such a
line parses cleanly and then fails when the file is imported - four subprocesses
away from the mistake.

Every type gets an explicit rule. Anything unrecognised raises instead of being
guessed at, because a wrong guess produces source that parses and means
something else, which is the one failure mode worth engineering against here.
"""

import math
from datetime import date, datetime, time, timedelta


class Raw(str):
    """Source text to be written out verbatim, for what no value can express.

    The case this exists for is a setting whose value refers to another variable
    in the file, such as
    `day_of_week_entry_start_time = {0: trade_start_time, ...}`.

    Nothing checks it. A malformed fragment leaves the constants file
    unimportable, so use a real value wherever one will do - concrete times in
    that dict format correctly without this.
    """


def as_source(value) -> str:
    """Format one config value as Python source for the constants file."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(
            f"{value!r} has no source form: `inf` and `nan` are not builtins, so "
            "writing one would leave the constants file unimportable."
        )
    if isinstance(value, Raw):  # before str: Raw subclasses it
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)
    if isinstance(value, datetime):  # before date: datetime subclasses date
        return (
            f"datetime({value.year}, {value.month}, {value.day}, "
            f"{value.hour}, {value.minute}, {value.second})"
        )
    if isinstance(value, date):
        return f"date({value.year}, {value.month}, {value.day})"
    if isinstance(value, time):
        return f"time({value.hour}, {value.minute}, {value.second})"
    if isinstance(value, timedelta):
        return f"timedelta(seconds={value.total_seconds():g})"
    if isinstance(value, dict):
        items = ", ".join(f"{as_source(k)}: {as_source(v)}" for k, v in value.items())
        return "{" + items + "}"
    if isinstance(value, list):
        return "[" + ", ".join(as_source(v) for v in value) + "]"
    if isinstance(value, tuple):
        items = ", ".join(as_source(v) for v in value)
        return f"({items},)" if len(value) == 1 else f"({items})"
    raise TypeError(
        f"no source form defined for {type(value).__name__}: {value!r}. "
        "Add an explicit rule rather than letting the value be guessed at."
    )


def as_assignments(config: dict) -> dict[str, str]:
    """A whole config as {parameter: source text}, ready to be written out."""
    return {name: as_source(value) for name, value in config.items()}
