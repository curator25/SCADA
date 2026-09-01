import threading                          # Runs as a background thread
from datetime import datetime, timedelta  # Interval boundaries

from .db import METRICS
from .timeutil import resolve_timezone

# --------------------------------------------------------------------------- #
# Performance Ratio (temperature-corrected, IEC 61724-1 style)
#
#            SUM(E_ac)
#   PR = ----------------------------------------------
#         P_STC * SUM[(1 - L_t) * G_poa * (1 kW/m2)^-1]
#
# Mirrors the plant's own SCADA function-block logic:
#
#   Temperature   = (Tmod_monthly - Tmeas) * -0.29          [% loss]
#   Cal_Temp_Irr  = (POA / 12000) * (1 - Temperature/100)
#   PR            = Energy / (Cal_Temp_Irr * P_STC) * 100 * 1000
#   APR           = SEL(PR > 100, PR, 0.0)
#
# The 12000 divisor is 1000 W/m2 (STC reference) x 12 five-minute slices per
# hour, so it is derived from the configured interval rather than hardcoded -
# change interval_minutes to 15 and it becomes 1000 x 4 = 4000, which keeps
# the units correct instead of silently scaling PR by 3x.
# --------------------------------------------------------------------------- #

STATUS_OK = "ok"
STATUS_BELOW_THRESHOLD = "below_threshold"   # Night / heavy cloud - gate is closed
STATUS_INSUFFICIENT_DATA = "insufficient_data"
STATUS_BAD_INPUT = "bad_input"               # Would divide by zero
STATUS_NO_ENERGY = "no_energy"
STATUS_OVER_RANGE = "over_range"             # The SEL guard fired
STATUS_IMPLAUSIBLE = "implausible_input"     # Reading is outside physical limits

# How the energy register should be read - see PRCalculator._energy
ENERGY_MODES = ("interval_sum", "interval_total", "cumulative")


def validate_kpi_config(cfg, logger):
    """Check the KPI settings once at startup and report what is wrong.

    Without this, a bad setting shows up only as a generic failure every five
    minutes forever - e.g. a short tmod_monthly list logged an error on every
    single cycle. Returns the list of problems (empty when all is well).
    """
    problems = []
    parameter_names = {p.get("name") for p in cfg.parameters()}

    metrics = cfg.get("kpi", "metrics", default=[]) or []
    if not metrics:
        problems.append("kpi.metrics is empty - no KPI will be stored or shown")

    interval = cfg.get("kpi", "interval_minutes", default=5)
    if not isinstance(interval, int) or interval <= 0 or 60 % interval:
        problems.append(
            f"kpi.interval_minutes ({interval}) should divide 60 evenly, "
            f"otherwise intervals drift across the hour"
        )

    pr_cfg = cfg.get("kpi", "pr", default={}) or {}

    p_stc = pr_cfg.get("p_stc_kwp")
    if not isinstance(p_stc, (int, float)) or p_stc <= 0:
        problems.append(f"kpi.pr.p_stc_kwp ({p_stc}) must be a positive number")

    monthly = pr_cfg.get("tmod_monthly") or []
    if len(monthly) != 12 or not all(isinstance(v, (int, float)) for v in monthly):
        problems.append(
            f"kpi.pr.tmod_monthly must hold 12 numbers (Jan-Dec), got {len(monthly)}"
        )

    mode = cfg.get("kpi", "energy_mode", default="interval_total")
    if mode not in ENERGY_MODES:
        problems.append(
            f"kpi.energy_mode is '{mode}' - must be one of {', '.join(ENERGY_MODES)}"
        )

    reference = pr_cfg.get("irradiance_ref", 1000)
    if not isinstance(reference, (int, float)) or reference <= 0:
        problems.append(f"kpi.pr.irradiance_ref ({reference}) must be positive")

    # Every parameter the maths reads must actually be configured and polled
    for key, default in (("irradiance_param", "poa_avg"),
                         ("module_temp_param", "t_mod"),
                         ("energy_param", "energy_05min")):
        name = pr_cfg.get(key, default)
        if name not in parameter_names:
            problems.append(
                f"kpi.pr.{key} is '{name}', which is not in the parameters list"
            )

    for problem in problems:
        logger.error(f"KPI config: {problem}")

    return problems


class PRCalculator(threading.Thread):
    """Computes the Performance Ratio once per interval and stores it.

    Runs `compute_delay_seconds` AFTER the interval boundary. That offset is
    not cosmetic: the MinuteWriter wakes on the same boundary, so without it
    this thread would race the final minute row into the database and read
    four samples instead of five, every single time.
    """

    def __init__(self, store, database, buffer, cfg, logger, stop_event):
        super().__init__(name="pr-calculator", daemon=True)
        self.store = store
        self.db = database
        self.buffer = buffer
        self.cfg = cfg
        self.logger = logger
        self.stop_event = stop_event

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _tz(self):
        """Timezone the stored timestamps use (config `timezone`)."""
        return resolve_timezone(self.cfg.get("timezone", default="UTC"), self.logger)

    def _month_tz(self):
        """Timezone that decides which monthly reference temperature applies.

        Separate from the storage timezone: timestamps may be stored in UTC,
        but "which month is it" should follow the plant's local calendar.
        Falls back to the storage timezone when not set.
        """
        name = self.cfg.get("kpi", "month_timezone", default=None)
        if not name:
            return self._tz()
        return resolve_timezone(name, self.logger, fallback=self._tz())

    def _column_for(self, param_name):
        """Map a parameter name from config to its database column name."""
        for param in self.cfg.parameters():
            if param.get("name") == param_name:
                return param.get("column") or param.get("name")
        return None

    @staticmethod
    def _floor_to_interval(moment, minutes):
        """Round a time down to the previous interval boundary."""
        return moment.replace(
            minute=(moment.minute // minutes) * minutes, second=0, microsecond=0
        )

    @staticmethod
    def _mean(values):
        return sum(values) / len(values) if values else None

    @staticmethod
    def _column_values(rows, column):
        """Every non-null value of one column across the window."""
        if not column:
            return []
        return [r[column] for r in rows if r.get(column) is not None]

    def _rows_for_window(self, start, end, plant):
        """Minute rows for this interval - database first, memory as fallback."""
        rows = self.db.fetch_window(start, end, plant)

        if rows is None:            # Database unreachable
            rows = self.buffer.window(start, end)
            if rows:
                self.logger.warning(
                    f"KPI: database unavailable, using {len(rows)} buffered row(s)"
                )
        elif not rows:              # Reachable but empty (e.g. rows still spooled)
            rows = self.buffer.window(start, end)

        return rows or []

    # ------------------------------------------------------------------ #
    # the calculation
    # ------------------------------------------------------------------ #
    def _compute(self, window_end):
        """Return (pr_value, status, details) for the interval ending at window_end.

        `details` carries the intermediate values. They are not stored - the
        table holds only the KPIs themselves - but they are logged so a wrong
        PR can still be traced. The raw inputs remain in kpi_readings, so any
        interval can be re-derived from scratch.
        """
        pr_cfg = self.cfg.get("kpi", "pr", default={}) or {}
        interval = self.cfg.get("kpi", "interval_minutes", default=5)
        min_samples = self.cfg.get("kpi", "min_samples", default=3)
        plant = self.cfg.get("plant", "name", default="UNKNOWN")

        window_start = window_end - timedelta(minutes=interval)
        rows = self._rows_for_window(window_start, window_end, plant)

        poa_col = self._column_for(pr_cfg.get("irradiance_param", "poa_avg"))
        tmod_col = self._column_for(pr_cfg.get("module_temp_param", "t_mod"))
        energy_col = self._column_for(pr_cfg.get("energy_param", "energy_05min"))

        poa_values = self._column_values(rows, poa_col)
        tmod_values = self._column_values(rows, tmod_col)
        energy_values = self._column_values(rows, energy_col)

        details = {"samples": len(rows)}

        # ---- enough data to trust? ----
        if len(poa_values) < min_samples:
            return None, STATUS_INSUFFICIENT_DATA, details

        # ---- irradiance gate: IF Irradiance > 50 ----
        poa = self._mean(poa_values)
        details["poa"] = poa
        threshold = pr_cfg.get("irradiance_threshold", 50)

        if poa <= threshold:
            return None, STATUS_BELOW_THRESHOLD, details

        # ---- is the reading physically possible? ----
        # Peak terrestrial irradiance is ~1000 W/m2, so anything far above that
        # is a wrong register, wrong word order, or a simulator - not weather.
        # Saying so explicitly beats letting it fall through to a generic
        # arithmetic failure five minutes later.
        poa_max = pr_cfg.get("poa_max", 2000)
        if poa_max and poa > poa_max:
            self.logger.error(
                f"KPI: POA {poa:.1f} W/m2 exceeds the {poa_max} W/m2 limit - "
                f"check the register mapping for '{pr_cfg.get('irradiance_param')}'"
            )
            return None, STATUS_IMPLAUSIBLE, details

        if len(tmod_values) < min_samples:
            return None, STATUS_INSUFFICIENT_DATA, details

        # ---- temperature correction ----
        tmeas = self._mean(tmod_values)
        details["tmeas"] = tmeas

        tmod_min = pr_cfg.get("tmod_min", -40)
        tmod_max = pr_cfg.get("tmod_max", 120)
        if not (tmod_min <= tmeas <= tmod_max):
            self.logger.error(
                f"KPI: module temperature {tmeas:.1f} C is outside "
                f"{tmod_min}..{tmod_max} C - check the register mapping for "
                f"'{pr_cfg.get('module_temp_param')}'"
            )
            return None, STATUS_IMPLAUSIBLE, details

        monthly = pr_cfg.get("tmod_monthly") or []
        month = datetime.now(self._month_tz()).month

        if len(monthly) < 12:
            self.logger.error("KPI: tmod_monthly needs 12 values - cannot compute PR")
            return None, STATUS_BAD_INPUT, details

        tref = monthly[month - 1]
        coefficient = pr_cfg.get("temp_coefficient", -0.29)

        loss_percent = (tref - tmeas) * coefficient
        details["tref"] = tref

        # ---- temperature-corrected reference irradiation for this interval ----
        reference = pr_cfg.get("irradiance_ref", 1000)
        slices_per_hour = 60 / interval                 # 5 min -> 12
        divisor = reference * slices_per_hour           # -> 12000

        cal_temp_irr = (poa / divisor) * (1 - loss_percent / 100.0)
        details["cal_temp_irr"] = cal_temp_irr

        if cal_temp_irr <= 0:
            return None, STATUS_BAD_INPUT, details

        p_stc = pr_cfg.get("p_stc_kwp", 0)
        expected_kwh = cal_temp_irr * p_stc
        details["expected_kwh"] = expected_kwh

        if expected_kwh <= 0:
            return None, STATUS_BAD_INPUT, details

        # ---- actual energy for this interval ----
        anchor = None
        energy_mode = self.cfg.get("kpi", "energy_mode", default="interval_total")
        if energy_mode == "cumulative":
            anchor = self._energy_anchor(window_start, plant, energy_col, interval)
            details["meter_start"] = anchor
            if anchor is None:
                self.logger.warning(
                    "KPI: no meter reading at the start of the interval - "
                    "cumulative energy needs the previous boundary, so this "
                    "interval is skipped (normal for the first one after a start)"
                )

        energy_mwh = self._energy(energy_values, anchor, interval)
        if energy_mwh is None:
            return None, STATUS_NO_ENERGY, details

        details["energy_mwh"] = energy_mwh

        # A plant cannot generate more than its nameplate rating for the whole
        # interval. Anything beyond that is a wrong register or the wrong
        # energy_mode (a cumulative meter read as an interval total), and the
        # 'PR > 100 -> 0.0' guard would otherwise hide it as a plain zero.
        factor = pr_cfg.get("energy_max_factor", 1.5)
        if factor:
            ceiling = p_stc * (interval / 60.0) / 1000.0 * factor    # MWh
            if energy_mwh > ceiling:
                self.logger.error(
                    f"KPI: energy {energy_mwh:.3f} MWh in {interval} min exceeds "
                    f"the {ceiling:.3f} MWh ceiling for a {p_stc} kWp plant - "
                    f"check the register for '{pr_cfg.get('energy_param')}' "
                    f"and kpi.energy_mode"
                )
                return None, STATUS_IMPLAUSIBLE, details

        # ---- PR, as a percentage ----
        # Energy is MWh and expected is kWh, hence the x1000 - this is the
        # "* 100 * 1000" pair in the SCADA diagram, split so the units are
        # visible rather than folded into one magic constant.
        pr = (energy_mwh * 1000.0) / expected_kwh * 100.0

        # ---- SEL guard: implausible PR is reported as 0, not clamped ----
        pr_max = pr_cfg.get("pr_max_valid", 100)
        if pr > pr_max or pr < 0:
            details["raw_pr"] = pr
            return 0.0, STATUS_OVER_RANGE, details

        return pr, STATUS_OK, details

    def _energy_anchor(self, window_start, plant, energy_col, interval):
        """Meter reading AT the interval's start boundary, for cumulative mode.

        Looks back up to one interval, so a single missing minute row does not
        void the calculation.
        """
        rows = self._rows_for_window(
            window_start - timedelta(minutes=interval), window_start, plant
        )
        values = self._column_values(rows, energy_col)
        return values[-1] if values else None

    def _energy(self, energy_values, anchor=None, interval=5):
        """Energy generated during the interval, in MWh.

        Three shapes of source register are supported, because plants differ:

          interval_sum    the register reports the energy of each MINUTE, so
                          the interval total is those samples added up
          interval_total  the register already holds the interval's total, so
                          the value at the boundary is taken as-is
          cumulative      a lifetime meter, so the interval's energy is the
                          rise from the reading at the start boundary
        """
        if not energy_values:
            return None

        mode = self.cfg.get("kpi", "energy_mode", default="interval_total")

        if mode == "interval_sum":
            # Scaling the mean by the interval equals a plain sum when every
            # minute is present, but does not silently under-report when one
            # is missing - a 3-of-5 window would otherwise return 60% of the
            # real energy and drag PR down with no visible cause.
            per_minute = self._mean(energy_values)
            if per_minute is None:
                return None
            if len(energy_values) < interval:
                self.logger.warning(
                    f"KPI: only {len(energy_values)} of {interval} energy samples "
                    f"in this interval - total estimated from the average minute"
                )
            return per_minute * interval

        if mode == "cumulative":
            # A lifetime meter only reveals the interval's energy by difference,
            # and the subtraction has to start from the reading at the START
            # boundary - not the first reading inside the window.
            #
            # The window for 09:05 holds 09:01..09:05, so differencing within it
            # spans four minutes, not five: PR would come out ~20% low, and
            # plausibly enough that nobody would question it.
            if anchor is None:
                return None
            delta = energy_values[-1] - anchor
            return delta if delta >= 0 else None     # Negative = meter rollover/reset

        # interval_total: the register already holds this interval's energy, so
        # take the value at the boundary. Summing the samples would multiply the
        # result by the number of minutes in the window.
        return energy_values[-1]

    # ------------------------------------------------------------------ #
    # main loop
    # ------------------------------------------------------------------ #
    def _build_row(self, window_end, values):
        """One wide row: Timestamp + one column per configured KPI."""
        ts_col = self.cfg.get("database", "timestamp_column", default="Timestamp")
        row = {ts_col: window_end}

        for metric in self.cfg.get("kpi", "metrics", default=[]) or []:
            name = metric.get("name")
            column_name = metric.get("column") or name
            row[column_name] = values.get(name)     # None -> NULL when not computable

        return row

    def _run_once(self, window_end):
        """Compute, persist and publish one interval."""
        try:
            pr, status, details = self._compute(window_end)
        except Exception as e:
            self.logger.error(f"KPI: PR calculation failed: {e}")
            self.store.hold_kpi("pr", STATUS_BAD_INPUT)
            return

        row = self._build_row(window_end, {"pr": pr})

        # Persist. KPI rows get no retry spool - unlike raw readings they can
        # always be recomputed from kpi_readings, so a failed insert is a
        # logged gap rather than lost data.
        if self.cfg.get("database", "enabled", default=True):
            try:
                self.db.insert_row(row, table_key=METRICS)
            except Exception as e:
                self.logger.error(f"KPI: could not store row: {e}")

        # The inputs are not stored, so log them - this is what makes a
        # surprising PR traceable after the fact.
        trace = " ".join(
            f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}"
            for k, v in details.items()
        )

        if pr is None:
            # No new value this cycle: keep showing the previous PR, flagged stale.
            self.store.hold_kpi("pr", status)
            self.logger.info(
                f"PR not computed at {window_end:%Y-%m-%d %H:%M} ({status}) [{trace}]"
            )
        else:
            self.store.publish_kpi("pr", pr, status, window_end)
            self.logger.info(
                f"PR {pr:.2f}% at {window_end:%Y-%m-%d %H:%M} ({status}) [{trace}]"
            )

    def run(self):
        self.logger.info("PR calculator started")

        if self.cfg.get("kpi", "backfill_on_start", default=True):
            # Compute the most recent completed interval so the panel shows a
            # value immediately after a restart instead of '--' for 5 minutes.
            try:
                interval = self.cfg.get("kpi", "interval_minutes", default=5)
                now = datetime.now(self._tz()).replace(tzinfo=None)
                self._run_once(self._floor_to_interval(now, interval))
            except Exception as e:
                self.logger.error(f"KPI: backfill on start failed: {e}")

        while not self.stop_event.is_set():
            interval = self.cfg.get("kpi", "interval_minutes", default=5)
            delay = self.cfg.get("kpi", "compute_delay_seconds", default=10)

            if not isinstance(interval, int) or interval <= 0:
                interval = 5                            # Never divide by zero below
            period = interval * 60

            # Sleep to the next boundary, then the settling delay on top
            now = datetime.now(self._tz())
            seconds_into = (now.minute * 60 + now.second) % period
            sleep_for = period - seconds_into - now.microsecond / 1e6 + delay

            if self.stop_event.wait(sleep_for):
                break                                   # Shutting down

            if not self.cfg.get("kpi", "enabled", default=True):
                continue                                # KPI calc switched off in config

            # An unexpected error must not kill the calculator - PR would stop
            # updating with everything else still looking healthy.
            try:
                # We woke just past a boundary, so flooring gives that boundary
                now = datetime.now(self._tz()).replace(tzinfo=None)
                self._run_once(self._floor_to_interval(now, interval))
            except Exception as e:
                self.logger.error(f"KPI: interval failed: {e}")

        self.logger.info("PR calculator stopped")
