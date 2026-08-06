from datetime import datetime                            # Parse ?from= / ?to=

from fastapi import FastAPI, HTTPException, Query, Response   # Web framework
from fastapi.middleware.cors import CORSMiddleware       # Allow the SCADA page to call us

from .reports import five_minute_report, daily_report


def create_app(store, cfg, logger, database=None, source=None):
    """Build the FastAPI app that serves live values to the KPI panel.

    Every response is served from the in-memory LatestStore, so the panel can
    poll once per second without ever touching the database.
    """
    app = FastAPI(title="KPI Backend", version="2.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.get("api", "cors_origins", default=["*"]),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    def _active_source():
        """Which configured source is currently supplying values, if any."""
        return getattr(source, "active_name", None) if source is not None else None

    @app.get("/health")
    def health():
        """Liveness probe - also reports whether the data source is connected."""
        snapshot = store.snapshot()
        return {
            "status": "ok",
            # Kept under the original name so anything already reading it -
            # scripts, the container health check - keeps working.
            "modbus_connected": snapshot["connected"],
            "source_connected": snapshot["connected"],
            "active_source": _active_source(),
            "last_update": snapshot["updated_at"].isoformat() if snapshot["updated_at"] else None,
        }

    def _parameter_rows(snapshot):
        """Live Modbus parameters, in panel display order.

        Parameters marked `"display": false` are read and stored like any
        other, but skipped here - they feed the KPI maths rather than the
        panel (e.g. T° Mod and Energy 05Min).
        """
        rows = []

        for param in cfg.parameters():
            if param.get("display", True) is False:
                continue

            name = param.get("name")
            entry = snapshot["values"].get(name, {"value": None, "quality": "bad"})
            value = entry["value"]
            decimals = param.get("decimals", 3)

            rows.append(
                {
                    "name": name,
                    "label": param.get("label", name),          # Row text for the panel
                    "unit": param.get("unit", ""),
                    "value": value,
                    "display": f"{value:.{decimals}f}" if value is not None else "--",
                    "quality": entry["quality"],
                }
            )

        return rows

    def _kpi_rows(snapshot):
        """Computed KPIs, in the order they are listed in config.

        Driven by `kpi.metrics` exactly as the parameter rows are driven by
        `parameters`, so adding the next KPI to config makes it appear here
        with no frontend or API change.

        `stale` is true when this is a held value - the last good result being
        shown because the current interval produced nothing (night, or a data
        gap). The value is still returned so the panel can display it; the
        flag lets the UI mark it as not current.
        """
        rows = []

        for metric in cfg.get("kpi", "metrics", default=[]) or []:
            name = metric.get("name")
            entry = snapshot["kpis"].get(name) or {
                "value": None, "status": "pending", "computed_at": None, "stale": False
            }

            value = entry["value"]
            decimals = metric.get("decimals", 2)
            computed_at = entry.get("computed_at")

            rows.append(
                {
                    "name": name,
                    "label": metric.get("label", name),
                    "unit": metric.get("unit", ""),
                    "value": value,
                    "display": f"{value:.{decimals}f}" if value is not None else "--",
                    "quality": "good" if value is not None else "bad",
                    "status": entry.get("status"),
                    "stale": entry.get("stale", False),
                    "computed_at": computed_at.isoformat() if computed_at else None,
                }
            )

        return rows

    @app.get("/api/kpi")
    def kpi():
        """Current value of every visible parameter plus the computed KPIs.

        `value` is null when a reading is unavailable, so the UI can show
        '--' instead of a misleading 0.00.
        """
        snapshot = store.snapshot()

        return {
            "timestamp": snapshot["updated_at"].isoformat() if snapshot["updated_at"] else None,
            "modbus_connected": snapshot["connected"],      # legacy name, kept
            "source_connected": snapshot["connected"],
            "active_source": _active_source(),
            "parameters": _parameter_rows(snapshot),
            "kpis": _kpi_rows(snapshot),
        }

    # ------------------------------------------------------------------ #
    # CSV reports
    # ------------------------------------------------------------------ #
    def _parse_date(text, label):
        """Turn a ?from=/?to= value into a date, or None when absent."""
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"'{label}' must be a date in YYYY-MM-DD format, got '{text}'",
            )

    def _csv_response(builder, date_from, date_to):
        """Run a report builder and return it as a downloadable CSV."""
        if database is None:
            raise HTTPException(status_code=503, detail="Reporting is not available")

        filename, body = builder(database, cfg, date_from, date_to)

        if body is None:
            # fetch_metrics returned None - the database is unreachable
            raise HTTPException(status_code=503, detail="Database unavailable")

        return Response(
            content=body,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    @app.get("/api/reports/pr-5min")
    def report_5min(date_from: str = Query(None, alias="from"),
                    date_to: str = Query(None, alias="to")):
        """Every stored 5-minute PR value as CSV."""
        return _csv_response(
            five_minute_report, _parse_date(date_from, "from"), _parse_date(date_to, "to")
        )

    @app.get("/api/reports/pr-daily")
    def report_daily(date_from: str = Query(None, alias="from"),
                     date_to: str = Query(None, alias="to")):
        """One PR value per date - that date's last stored value - as CSV."""
        return _csv_response(
            daily_report, _parse_date(date_from, "from"), _parse_date(date_to, "to")
        )

    return app
