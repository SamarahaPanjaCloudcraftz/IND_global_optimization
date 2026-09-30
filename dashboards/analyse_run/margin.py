"""How much margin a job's config ties up, in relative units.

The ranking divides every variant's P&L by this before computing its metrics,
so variants that commit different amounts of capital compare fairly. Only the
ratio between variants matters, so the unit is arbitrary. To change the margin
model, change `margin_factor` — nothing else reads the rule.

Current rule:
  * margin is proportional to the units sold: signal weight x unit_size x
    tranches, summed over the selling weekdays;
  * a book unwound before its expiry day ties up half the margin of the same
    book held to expiry.

Tranches are counted the way the condor component opens them: on the session's
tick grid (every trade_interval minutes from the session open), from the entry
start to the entry end inclusive — and, when the book unwinds on the day it is
sold, only before unwind_time, because the component opens nothing once that
day's unwind time has passed.
"""

import math
from datetime import datetime

EXPIRY_DAY = 1.0
BEFORE_EXPIRY = 0.5


def _minutes(clock: str) -> int:
    t = datetime.strptime(clock, "%H:%M:%S")
    return t.hour * 60 + t.minute


def _unwinds_same_day(config: dict, day: int) -> bool:
    """True when a book sold on `day` unwinds that same day.

    A sale is (expiry - day) % 5 trading days from expiry, and the book unwinds
    unwind_trading_days_before trading days ahead of expiry — the same day when
    the two are equal. Nominal weekly cycle, as the tree's unwind domain is.
    """
    to_expiry = (config["expiry_day_of_week"] - day) % 5
    return config["unwind_trading_days_before"] == to_expiry


def tranches(config: dict, day: str) -> int:
    """Entry ticks inside the window: session open + k x interval, from the
    entry start (never before the session opens) to the entry end inclusive,
    and before the unwind time when the book unwinds that same day."""
    session = _minutes(config["trade_start_time"])
    interval = config["trade_interval_time"]
    start = max(_minutes(config["day_of_week_entry_start_time"][day]), session)
    first = math.ceil((start - session) / interval)
    last = (_minutes(config["day_of_week_entry_end_time"][day]) - session) // interval
    if _unwinds_same_day(config, int(day)):
        # Entries at or after the unwind time are not opened.
        last = min(last, math.ceil((_minutes(config["unwind_time"]) - session) / interval) - 1)
    return max(0, last - first + 1)


def units_sold(config: dict) -> float:
    """Units sold per week: weight x unit_size x tranches, over selling days."""
    total = 0.0
    for day, weight in config["day_of_week_signal_strength"].items():
        if weight:
            total += weight * config["unit_size"] * tranches(config, day)
    return total


def margin_factor(config: dict) -> float:
    expiry = EXPIRY_DAY if config["unwind_trading_days_before"] == 0 else BEFORE_EXPIRY
    return units_sold(config) * expiry
