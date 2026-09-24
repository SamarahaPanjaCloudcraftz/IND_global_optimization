"""SENSEX short vol, day-of-week condor.

Values only. The shape is inherited from `DowCondor`, shared with NIFTY, so a
structural change cannot land on one underlying and miss the other. Override any
method here if SENSEX ever needs an axis the others do not.
"""

from datetime import date, time

from .dow_condor import DowCondor


class Sensex(DowCondor):
    name = "SENSEX short vol DOW condor"
    underlying = "SENSEX"
    lot_size = 20
    unit_size_default = 40
    expiry_weekday = 3          # Thursday
    data_dir = "/home/oem/Documents/unit_tasks/IND_backtest/SENSEX/"
    period = (date(2025, 9, 5), date(2026, 5, 15))
    trade_start = time(9, 20, 0)
    trade_end = time(15, 29, 0)
    steps = 100
    otm_outstrike = 4
    signal_strength = {0: 2.5, 1: 2.5, 2: 0, 3: 0, 4: 0}
    hedge_pct = [1.0]
    gamma_threshold = 0.04

    gamma_threshold_ranges = {
        0: (-1, -4, 5), 1: (-2, -5, 5), 2: (-3, -6, 5),
        3: (-4.5, -8.5, 5), 4: (-0.5, -2.5, 5),
    }
    hedge_constant_ranges = {
        "gamma":    {0: (200_000, 20_000_000, 10), 1: (200_000, 20_000_000, 10),
                     2: (400_000, 40_000_000, 10), 3: (400_000, 40_000_000, 10),
                     4: (200_000, 20_000_000, 10)},
        "gamma_iv": {0: (6_500, 650_000, 10),   1: (6_500, 650_000, 10),
                     2: (13_000, 1_300_000, 10), 3: (26_000, 2_600_000, 10),
                     4: (6_500, 650_000, 10)},
        "static":   {day: (5, 25, 10) for day in range(5)},   # bps
    }


STRATEGY = Sensex()
