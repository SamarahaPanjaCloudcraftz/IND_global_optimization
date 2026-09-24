"""The day-of-week condor, as a base class each underlying subclasses.

NIFTY and SENSEX run the same strategy on different instruments, so the shape
belongs here and only the values belong in the subclasses. Two copies of one
structure drift, and nothing notices when they do.

Every piece is a method or property, so an underlying that genuinely needs a
different axis overrides that one piece and inherits the rest — rather than the
choice being all-or-nothing between sharing and forking the whole tree.

Anything derivable is derived rather than stated twice: the expiry weekday gives
the expiry name and the legal unwind offsets, the session times give the default
entry windows, and the OTM outstrike gives the static wing percentage. A fact
stated once cannot disagree with itself.
"""

from datetime import date, time

from engine import Internal, Leaf, Node, PRODUCT, SUM

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


class DowCondor:
    """Subclass per underlying: supply the values, override what differs."""

    # ---- what a subclass must supply ---------------------------------------
    name: str
    underlying: str
    lot_size: int
    expiry_weekday: int          # 0=Mon .. 4=Fri; also gives expiry_info
    data_dir: str
    period: tuple[date, date]
    trade_start: time
    trade_end: time
    steps: int
    otm_outstrike: float         # also the static wing percentage
    unit_size_default: int       # the sidebar's starting value
    signal_strength: dict
    hedge_pct: list
    gamma_threshold: float

    # ---- shape, shared unless a subclass rebinds it -------------------------
    runner = "tradelib_runner:run_one"
    strategy_class = "DAY_OF_WEEK_STATIC_CONDOR_STRATEGY"
    hedge_modes = ("static", "gamma", "gamma_iv")
    unwind_time = time(14, 50, 0)
    # The delta hedge the whole tree is measured against: static mode at 16
    # basis points, every weekday, on both underlyings. Every axis holds this
    # while it varies its own parameters, so it is the thing each result is a
    # departure from.
    baseline_hedge_mode = "static"
    hedge_constant = {day: 16 for day in range(5)}
    # Settings both underlyings agree on. They are pinned rather than left to
    # the constants file, which every run rewrites in place: an unpinned value
    # is whatever the previous run happened to leave behind, and several of
    # these differ between the two simulators.
    trade_interval_time = 3
    exchange_fee_rate_option = 0.001
    underlying_threshold_hedge_flag = True
    underlying_threshold_hedge_gamma_source = "total"
    underlying_threshold_basis_points = 11
    free_hedge_after = {"expiry": time(17, 0, 0)}
    gamma_hedge_start_time = time(9, 17, 0)
    gamma_hedge_stop_time = time(15, 30, 0)
    gamma_hedge_iv_thr_lower = 0.0
    gamma_hedge_iv_thr_upper = 10.0
    gamma_hedge_iv_decision = "upper"
    trade_with_received_money = False
    allow_frac_gamma_hedge = False
    hard_limit = 5

    # Sweep starting points that carry no instrument scale. Delta values are
    # DELTA x 100; static values are a PERCENT of the strike.
    wing_delta = 15
    short_delta = 30
    short_static = 2

    # ---- derived ------------------------------------------------------------

    @property
    def entry_start(self) -> dict:
        return {day: self.trade_start for day in range(7)}

    @property
    def entry_end(self) -> dict:
        return {day: self.trade_end for day in range(7)}

    @property
    def strangle_base(self) -> dict:
        return {"type": "atm", "value": None}

    @property
    def wing_base(self) -> dict:
        return {"type": "static", "value": self.otm_outstrike}

    @property
    def wing_off(self) -> dict:
        return {"type": "none", "value": None}

    def sell_only(self, weekday: int) -> dict:
        """Sell on this weekday alone, so a sweep measures that day by itself."""
        return {day: (1 if day == weekday else 0) for day in range(5)}

    def unwind_offsets(self, weekday: int) -> list[int]:
        """Offsets that still leave the position open when it is sold.

        A position sold on `weekday` reaches expiry after this many trading
        days; unwinding earlier closes the book before the trade is opened, so
        the run completes and takes no trades. Equal to the gap is legal — it
        unwinds on the selling day. This is the nominal weekly cycle, so a
        holiday-shifted expiry can leave a ceiling value idle for that week.
        """
        return list(range((self.expiry_weekday - weekday) % 5 + 1))

    # ---- hedge optimization -------------------------------------------------

    def gamma_day(self, weekday: int) -> Internal:
        """Gamma hedging measured on one selling weekday.

        None of these parameters is weekday-keyed in the engine, so each is a
        plain scalar applying to the whole run. The weekday is established by
        the one-hot signal — only that day sells — which is what makes the
        branch a measurement of that day.
        """
        return Internal(WEEKDAY_NAMES[weekday], PRODUCT, [
            Leaf("day_of_week_signal_strength", [self.sell_only(weekday)],
                 domain=[self.sell_only(weekday)]),
            # The whole gamma component sits behind `if gamma_hedge:`. Choosing
            # this axis is the decision to hedge, so it is pinned on rather than
            # offered: off, every job here would be the same unhedged run.
            # The baseline keeps it False, so the control stays unhedged.
            Leaf("gamma_hedge", [True], domain=[True]),
            Leaf("gamma_threshold", [self.gamma_threshold]),
            # The same config key the delta hedge uses. One constant sizes both
            # components — gamma_hedge_component multiplies its hedge ratio by
            # it unconditionally, while the delta side only consults it when
            # custom_pct_to_hedge is on.
            Leaf("percent_hedge", list(self.hedge_pct)),
            # Delta x 100, not a strike distance. See Backtest_engine_constraints.md
            Leaf("gamma_hege_otm_outstrike", [50]),
            Leaf("gamma_hedge_trade_direction", ["both"],
                 domain=["both", "buy", "sell"]),
        ])

    def gamma_hedging(self) -> Internal:
        return Internal("gamma_hedging", SUM,
                        [self.gamma_day(day) for day in range(5)])

    def hedge_day(self, mode: str, weekday: int) -> Internal:
        """One weekday under one hedging mode: its k values against the pct."""
        return Internal(WEEKDAY_NAMES[weekday], PRODUCT, [
            Leaf("underlying_threshold_hedge_type", [mode], domain=[mode]),
            Leaf("underlying_threshold_hedge_constant", [dict(self.hedge_constant)]),
            Leaf("percent_hedge", list(self.hedge_pct)),
            Leaf("custom_pct_to_hedge", [True], domain=[False, True]),
            Leaf("day_of_week_signal_strength", [self.sell_only(weekday)],
                 domain=[self.sell_only(weekday)]),
        ])

    def hedge_mode(self, mode: str) -> Internal:
        """One hedging mode, holding its own k values for each weekday.

        Mode is a sum rather than a product with k because the k ranges are
        per-mode: a static threshold and a gamma threshold are on entirely
        different scales, so no single range could be swept across them.
        """
        return Internal(mode, SUM, [self.hedge_day(mode, day) for day in range(5)])

    def delta_hedging(self) -> Internal:
        return Internal("delta_hedging", SUM,
                        [self.hedge_mode(mode) for mode in self.hedge_modes])

    # ---- sell optimization --------------------------------------------------

    def legs_day(self, weekday: int, strangle: dict, wing: dict,
                 sweeping: str) -> Internal:
        """One selling weekday with exactly one leg group free to vary.

        The other is pinned with a single-value domain, so it reads as fixed and
        cannot be swept by accident. Sweeping both would stop either being a
        measurement of the thing its branch is named after.
        """
        def leg(name: str, value: dict, free: bool) -> Leaf:
            return (Leaf(name, [dict(value)]) if free
                    else Leaf(name, [dict(value)], domain=[dict(value)]))

        return Internal(WEEKDAY_NAMES[weekday], PRODUCT, [
            Leaf("day_of_week_signal_strength", [self.sell_only(weekday)],
                 domain=[self.sell_only(weekday)]),
            leg("dow_strangle_leg", strangle, sweeping == "strangle"),
            leg("dow_wing_leg", wing, sweeping == "wing"),
        ])

    def legs(self, name: str, strangle: dict, wing: dict, sweeping: str) -> Internal:
        """One selection method across the five selling weekdays.

        Weekday-branched because days to expiry differ by weekday — a Monday
        sale is one day out, a Wednesday four — so the same delta or percentage
        is a different instrument on each, and the best choice need not agree.
        """
        return Internal(name, SUM, [self.legs_day(day, strangle, wing, sweeping)
                                    for day in range(5)])

    def condor(self) -> Internal:
        """Six ways of choosing strikes, each varying ONE leg group."""
        return Internal("condor_OTM_outstrike", SUM, [
            self.legs("static wings", self.strangle_base,
                      {"type": "static", "value": self.otm_outstrike}, "wing"),
            self.legs("delta wings", self.strangle_base,
                      {"type": "delta", "value": self.wing_delta}, "wing"),
            self.legs("static short", {"type": "static", "value": self.short_static},
                      self.wing_base, "strangle"),
            self.legs("delta short", {"type": "delta", "value": self.short_delta},
                      self.wing_base, "strangle"),
            self.legs("static short no wings",
                      {"type": "static", "value": self.short_static},
                      self.wing_off, "strangle"),
            self.legs("delta short no wings",
                      {"type": "delta", "value": self.short_delta},
                      self.wing_off, "strangle"),
        ])

    def trade_day(self, weekday: int) -> Internal:
        """One weekday's short-condor selling window, inside the session bounds.

        `day_of_week_entry_*` gate new condor tranches only — hedging and the
        unwind are untouched — whereas `trade_*_time` bound the whole session.
        Only this weekday's entry varies; with a one-hot signal no other day
        sells, so the other keys never apply.
        """
        return Internal(WEEKDAY_NAMES[weekday], PRODUCT, [
            Leaf("day_of_week_signal_strength", [self.sell_only(weekday)],
                 domain=[self.sell_only(weekday)]),
            Leaf("day_of_week_entry_start_time", [dict(self.entry_start)]),
            Leaf("day_of_week_entry_end_time", [dict(self.entry_end)]),
            Leaf("trade_start_time", [self.trade_start]),
            Leaf("trade_end_time", [self.trade_end]),
        ])

    def trade_time(self) -> Internal:
        return Internal("trade_time", SUM, [self.trade_day(day) for day in range(5)])

    def sell_day(self, weekday: int) -> Internal:
        """One selling weekday, against the shared unwind settings."""
        return Internal(WEEKDAY_NAMES[weekday], PRODUCT, [
            Leaf("day_of_week_signal_strength", [self.sell_only(weekday)],
                 domain=[self.sell_only(weekday)]),
            Leaf("unwind_time", [self.unwind_time]),
            # Every legal offset by default: the whole point of the axis is to
            # find which one pays, and the domain already excludes the ones
            # that would unwind before the trade is opened. The baseline stays
            # at 0, so the control still holds to expiry.
            Leaf("unwind_trading_days_before", self.unwind_offsets(weekday),
                 domain=self.unwind_offsets(weekday)),
        ])

    def dow_signal_strength(self) -> Internal:
        return Internal("dow_signal_strength", SUM,
                        [self.sell_day(day) for day in range(5)])

    # ---- sweep ranges -------------------------------------------------------
    #
    # Defaults from the "ranges" sheet of `ind short vol.xlsx`, as
    # (low, high, how many). A range beats a typed-out list: these are
    # continuous quantities, and nobody wants to write ten hedge constants by
    # hand for each of fifteen mode-and-weekday branches.
    #
    # Looked up by leaf path, falling back to shorter prefixes and finally to
    # the bare parameter name, so a range that is the same everywhere is stated
    # once and one that differs per weekday is stated per weekday.

    percent_hedge_range = (0.5, 1.2, 8)      # sheet says 50-120, in percent
    gamma_percent_hedge_range = (0.25, 1.0, 5)   # the sheet's "pct gamma hedge"
    # Per weekday, because the sheet gives a different band for each.
    gamma_threshold_ranges: dict
    unwind_time_range = (time(10, 0), time(15, 0), 6)
    entry_start_range = (time(9, 17), time(11, 17), 3)
    entry_end_range = (time(12, 30), time(14, 30), 5)
    # Per selection method, the same across weekdays.
    leg_ranges = {
        "static wings": (3, 7, 4),
        "delta wings": (2, 10, 5),
        "static short": (0.0, 1.0, 10),
        "delta short": (10, 50, 5),
        "static short no wings": (0.0, 1.0, 10),
        "delta short no wings": (10, 50, 5),
    }
    # Per hedging mode, per weekday: {mode: {weekday: (low, high, count)}}.
    hedge_constant_ranges: dict
    @property
    def ranges(self) -> dict:
        """Sweep ranges keyed by leaf path, prefix, or bare parameter name."""
        found = {
            "percent_hedge": self.percent_hedge_range,
            # Same parameter, its own range on the gamma axis. The exact path
            # wins over the bare name, so the delta side keeps its own.
            "gamma_hedging/percent_hedge": self.gamma_percent_hedge_range,
            "unwind_time": self.unwind_time_range,
            "day_of_week_entry_start_time": self.entry_start_range,
            "day_of_week_entry_end_time": self.entry_end_range,
        }
        for day, span in self.gamma_threshold_ranges.items():
            found[f"gamma_hedging/{WEEKDAY_NAMES[day]}/gamma_threshold"] = span
        for method, span in self.leg_ranges.items():
            leg = ("dow_wing_leg" if method.endswith("wings")
                   and "short" not in method else "dow_strangle_leg")
            found[f"condor_OTM_outstrike/{method}/{leg}"] = span
        for mode, per_day in self.hedge_constant_ranges.items():
            for day, span in per_day.items():
                found[f"delta_hedging/{mode}/{WEEKDAY_NAMES[day]}/"
                      "underlying_threshold_hedge_constant"] = span
        return found

    # ---- what the dashboard reads -------------------------------------------

    @property
    def tree(self) -> Node:
        return Internal("space", SUM, [
            self.gamma_hedging(),
            self.delta_hedging(),
            self.condor(),
            self.trade_time(),
            self.dow_signal_strength(),
        ])

    @property
    def baseline(self) -> dict:
        return {
            # Backtest period - set in the dashboard, common to every run.
            "start_date": self.period[0],
            "end_date": self.period[1],
            # Instrument identity - pins the run regardless of what the shared
            # constants file was left holding by a previous run.
            "strategy_to_execute": self.strategy_class,
            "underlying": self.underlying,
            "lot_size": self.lot_size,
            "unit_size": self.unit_size_default,
            "trade_interval_time": self.trade_interval_time,
            "expiry_info": [(WEEKDAY_NAMES[self.expiry_weekday], 1)],
            "expiry_day_of_week": self.expiry_weekday,
            "data_dir": self.data_dir,
            # Swept parameters
            "gamma_hedge": False,
            "custom_pct_to_hedge": False,
            "gamma_threshold": self.gamma_threshold,
            "gamma_hege_otm_outstrike": 50,
            "gamma_hedge_trade_direction": "both",
            "underlying_threshold_hedge_type": self.baseline_hedge_mode,
            "underlying_threshold_hedge_constant": dict(self.hedge_constant),
            "percent_hedge": self.hedge_pct[0],
            "dow_strangle_leg": dict(self.strangle_base),
            "dow_wing_leg": dict(self.wing_base),
            "OTM_outstrike": self.otm_outstrike,
            "steps": self.steps,
            "delta_otm": False,
            "delta_outstrike": 30,
            "strangle_outstrike": 1,
            "trade_start_time": self.trade_start,
            "trade_end_time": self.trade_end,
            "day_of_week_entry_start_time": dict(self.entry_start),
            "day_of_week_entry_end_time": dict(self.entry_end),
            "day_of_week_signal_strength": dict(self.signal_strength),
            "unwind_time": self.unwind_time,
            "unwind_trading_days_before": 0,
            # Pinned so a run cannot inherit them from whichever sweep ran last.
            "exchange_fee_rate_option": self.exchange_fee_rate_option,
            "underlying_threshold_hedge_flag": self.underlying_threshold_hedge_flag,
            "underlying_threshold_hedge_gamma_source":
                self.underlying_threshold_hedge_gamma_source,
            "underlying_threshold_basis_points": self.underlying_threshold_basis_points,
            "free_hedge_after": dict(self.free_hedge_after),
            "gamma_hedge_start_time": self.gamma_hedge_start_time,
            "gamma_hedge_stop_time": self.gamma_hedge_stop_time,
            "gamma_hedge_iv_thr_lower": self.gamma_hedge_iv_thr_lower,
            "gamma_hedge_iv_thr_upper": self.gamma_hedge_iv_thr_upper,
            "gamma_hedge_iv_decision": self.gamma_hedge_iv_decision,
            "trade_with_received_money": self.trade_with_received_money,
            "allow_frac_gamma_hedge": self.allow_frac_gamma_hedge,
            "HARD_LIMIT": self.hard_limit,
        }

    @property
    def groups(self) -> dict:
        return {
            "Hedge optimization": ["gamma_hedging", "delta_hedging"],
            "Sell optimization": ["condor_OTM_outstrike", "trade_time",
                                  "dow_signal_strength", "IVWAP"],
        }

    @property
    def shared(self) -> dict:
        return {
            # One setting each, applied to every branch of the axis.
            # Not weekday-keyed in the engine, so one setting covers the axis.
            "gamma_hedging": ["percent_hedge", "gamma_hege_otm_outstrike",
                              "gamma_hedge_trade_direction"],
            "delta_hedging": ["custom_pct_to_hedge", "percent_hedge"],
            # One unwind setting each, applied to every selling weekday.
            "dow_signal_strength": ["unwind_time", "unwind_trading_days_before"],
            # The entry windows are per-weekday values but usually the same
            # everywhere, so they are offered shared with the toggle; the
            # session bounds multiply across every weekday's window.
            "trade_time": ["day_of_week_entry_start_time",
                           "day_of_week_entry_end_time",
                           "trade_start_time", "trade_end_time"],
            # Scoped to each selection method rather than the axis: the same
            # parameter name is a delta in one method, a percent in another and
            # a disabled leg in two more, so one axis-wide input would feed the
            # wrong units.
            "condor_OTM_outstrike/static wings": ["dow_wing_leg"],
            "condor_OTM_outstrike/delta wings": ["dow_wing_leg"],
            "condor_OTM_outstrike/static short": ["dow_strangle_leg"],
            "condor_OTM_outstrike/delta short": ["dow_strangle_leg"],
            "condor_OTM_outstrike/static short no wings": ["dow_strangle_leg"],
            "condor_OTM_outstrike/delta short no wings": ["dow_strangle_leg"],
        }

    @property
    def varies(self) -> dict:
        """Dict parameters whose branch varies one named key, not a weekday."""
        return {"dow_strangle_leg": "value", "dow_wing_leg": "value"}

    @property
    def baselines(self) -> dict:
        """Dict parameters whose non-swept keys actually matter, so the branch
        needs a baseline you can set. A weekday's entry window is not one: with
        a one-hot signal no other day sells, so its other keys never apply."""
        return {"delta_hedging": ["underlying_threshold_hedge_constant"]}

    @property
    def gated(self) -> dict:
        """get_pct_to_hedge() returns percent_hedge only when
        custom_pct_to_hedge is on, and 1.0 otherwise. With the gate off the
        input is hidden and the value pinned, so the config records what the
        engine actually did."""
        return {"percent_hedge": ("custom_pct_to_hedge", 1)}

    @property
    def pending(self) -> dict:
        """Axes named in the optimization table that cannot be built yet."""
        return {"IVWAP": "No constant, module or reference exists in the "
                         "backtest engine."}
