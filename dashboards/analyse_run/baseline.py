"""Which job a weekday branch is compared against.

The baseline for a weekday is the control config selling on that weekday alone:
gamma hedge off, static delta hedge at the control's threshold, full hedge. No
job is exactly that config, because the delta_hedging axis always switches
custom_pct_to_hedge on — but with percent_hedge = 1.0 the engine hedges 100%,
the same as with the flag off. So the static-hedge variant at the control's
threshold and percent_hedge 1.0 runs the identical backtest, and is used.

To change what a branch is compared against, change `find` — nothing else reads
the rule.
"""

import runs

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def find(found: list[runs.Job], configs: dict[str, dict], weekday: str):
    """The baseline job for a weekday, or None when the stage cannot supply one."""
    control = next((configs[j.digest] for j in found
                    if j.axis == runs.CONTROL and j.digest in configs), None)
    if control is None or weekday not in WEEKDAYS:
        return None
    key = str(WEEKDAYS.index(weekday))
    threshold = control["underlying_threshold_hedge_constant"][key]
    for job in found:
        config = configs.get(job.digest)
        if (job.axis == f"delta_hedging/static/{weekday}" and config
                and config["underlying_threshold_hedge_constant"][key] == threshold
                and config["percent_hedge"] == 1.0):
            return job
    return None
