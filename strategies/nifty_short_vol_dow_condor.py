"""NIFTY short vol, day-of-week condor.

Values only. The shape is inherited from `DowCondor`, shared with SENSEX, so a
structural change cannot land on one underlying and miss the other. Override any
method here if NIFTY ever needs an axis the others do not.
"""

from datetime import date, time

from .dow_condor import DowCondor


class Nifty(DowCondor):
    name = "NIFTY short vol DOW condor"
    underlying = "NIFTY"
    lot_size = 65
    unit_size_default = 260
    expiry_weekday = 1          # Tuesday
    data_dir = "/home/oem/Documents/unit_tasks/IND_short_vol/preproc_data/NIFTY/"
    period = (date(2026, 1, 1), date(2026, 9, 2))
    trade_start = time(9, 17, 0)
    trade_end = time(15, 30, 0)
    steps = 50
    otm_outstrike = 5
    signal_strength = {0: 3, 1: 2, 2: 0, 3: 0, 4: 0}
    hedge_pct = [0.5]
    gamma_threshold = 90

    gamma_threshold_ranges = {
        0: (-40, -80, 5), 1: (-60, -130, 5), 2: (-10, -50, 5),
        3: (-20, -60, 5), 4: (-20, -60, 5),
    }
    hedge_constant_ranges = {
        "gamma":    {0: (100_000, 10_000_000, 10), 1: (200_000, 20_000_000, 10),
                     2: (50_000, 5_000_000, 10),   3: (50_000, 5_000_000, 10),
                     4: (100_000, 10_000_000, 10)},
        "gamma_iv": {0: (6_500, 650_000, 10),  1: (13_000, 1_300_000, 10),
                     2: (2_600, 260_000, 10),  3: (2_600, 260_000, 10),
                     4: (2_600, 260_000, 10)},
        "static":   {day: (5, 25, 10) for day in range(5)},   # bps
    }


STRATEGY = Nifty()
