import csv                                     # CSV writing
import io                                      # Build the file in memory
from datetime import datetime, time, timedelta # Date handling

from .timeutil import resolve_timezone

# --------------------------------------------------------------------------- #
# CSV reports built from kpi_metrics.
#
# Neither report recalculates anything - the PR values are taken exactly as
# they were stored by the calculator:
#
#   5-minute report  one row per stored interval
#   daily report     one row per date, carrying that date's LAST PR value
#
# Timestamps are stored naive in the `timezone` from config, and rendered in
# `reports.timezone`. When the two match nothing shifts; when they differ the
# values are converted, which is also what decides the date boundaries in the
# daily report.
# --------------------------------------------------------------------------- #


def _timezones(cfg, logger=None):
    """(storage timezone, report timezone).

    Reports fall back to the storage timezone, so an absent `reports.timezone`
    means "print the timestamps exactly as stored".
    """
    storage_name = cfg.get("timezone", default="UTC")
    storage = resolve_timezone(storage_name, logger)
    report_name = cfg.get("reports", "timezone", default=None) or storage_name
    return storage, resolve_timezone(report_name, logger, fallback=storage)


def _to_report_zone(stamp, storage, report):
    """Reinterpret a stored timestamp in the report's timezone.

    Timestamps come back from the database naive, but a driver that returns
    them aware must not crash the export - localize() rejects aware values.
    """
    if stamp.tzinfo is None:
        stamp = storage.localize(stamp)
    return stamp.astimezone(report)


def _metric_column(cfg):
    """Column name of the first configured KPI (currently 'PR')."""
    metrics = cfg.get("kpi", "metrics", default=[]) or []
    if not metrics:
        return "PR", 2
    metric = metrics[0]
    return metric.get("column") or metric.get("name"), metric.get("decimals", 2)


def _local_rows(db, cfg, date_from, date_to):
    """Stored KPI rows paired with their timestamp in the report timezone.

    The SQL window is widened by a day on each side so a timezone shift can
    never clip rows that belong to a requested local date; the exact filtering
    is then done on the converted dates.
    """
    storage, report = _timezones(cfg, getattr(db, "logger", None))
    ts_col = cfg.get("database", "timestamp_column", default="Timestamp")

    start = datetime.combine(date_from, time.min) - timedelta(days=1) if date_from else None
    end = datetime.combine(date_to, time.max) + timedelta(days=1) if date_to else None

    rows = db.fetch_metrics(start, end)
    if rows is None:
        return None

    out = []
    for row in rows:
        stamp = row.get(ts_col)
        if stamp is None:
            continue
        local = _to_report_zone(stamp, storage, report)
        if date_from and local.date() < date_from:
            continue
        if date_to and local.date() > date_to:
            continue
        out.append((local, row))

    return out


def _filename(cfg, kind):
    """PR_<plant>_<5min|daily>.csv

    Spaces in the plant name become underscores so the download filename stays
    clean; everything else is used as configured.
    """
    plant = str(cfg.get("plant", "name", default="plant")).replace(" ", "_")
    return f"PR_{plant}_{kind}.csv"


def five_minute_report(db, cfg, date_from=None, date_to=None):
    """One row per stored 5-minute interval: Date, Time, PR (%).

    Intervals with no PR (night, below the irradiance threshold, data gaps)
    are omitted rather than exported as blanks.
    """
    rows = _local_rows(db, cfg, date_from, date_to)
    if rows is None:
        return None, None

    column, decimals = _metric_column(cfg)

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["Date", "Time", "PR (%)"])

    for local, row in rows:
        value = row.get(column)
        if value is None:
            continue
        writer.writerow([
            local.strftime("%Y-%m-%d"),
            local.strftime("%H:%M"),
            f"{value:.{decimals}f}",
        ])

    return _filename(cfg, "5min"), buffer.getvalue()


def daily_report(db, cfg, date_from=None, date_to=None):
    """One row per date: Date, PR (%), Valid Intervals.

    The date's PR is its LAST stored value, not an average or a recalculation.
    'Valid Intervals' counts how many intervals that day produced a PR, which
    is what tells you whether the last value represents a full day or a
    handful of readings.
    """
    rows = _local_rows(db, cfg, date_from, date_to)
    if rows is None:
        return None, None

    column, decimals = _metric_column(cfg)

    # Rows arrive oldest-first, so the last write for a date wins
    per_date = {}
    for local, row in rows:
        value = row.get(column)
        if value is None:
            continue
        day = local.date()
        entry = per_date.setdefault(day, {"value": None, "count": 0})
        entry["value"] = value          # Overwritten until the day's final row
        entry["count"] += 1

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["Date", "PR (%)", "Valid Intervals"])

    for day in sorted(per_date):
        entry = per_date[day]
        writer.writerow([
            day.strftime("%Y-%m-%d"),
            f"{entry['value']:.{decimals}f}",
            entry["count"],
        ])

    return _filename(cfg, "daily"), buffer.getvalue()
