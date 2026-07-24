from fastapi import FastAPI                              # Web framework
from fastapi.middleware.cors import CORSMiddleware       # Allow the SCADA page to call us


def create_app(store, cfg, logger):
    """Build the FastAPI app that serves live values to the KPI panel.

    Every response is served from the in-memory LatestStore, so the panel can
    poll once per second without ever touching the database.
    """
    app = FastAPI(title="KPI Backend", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.get("api", "cors_origins", default=["*"]),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health():
        """Liveness probe - also reports whether Modbus is currently connected."""
        snapshot = store.snapshot()
        return {
            "status": "ok",
            "modbus_connected": snapshot["connected"],
            "last_update": snapshot["updated_at"].isoformat() if snapshot["updated_at"] else None,
        }

    @app.get("/api/kpi")
    def kpi():
        """Current value of every configured parameter, in panel display order.

        `value` is null when the reading is unavailable, so the UI can show
        '--' instead of a misleading 0.00.
        """
        snapshot = store.snapshot()
        rows = []

        for param in cfg.parameters():
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

        return {
            "timestamp": snapshot["updated_at"].isoformat() if snapshot["updated_at"] else None,
            "modbus_connected": snapshot["connected"],
            "parameters": rows,
        }

    return app
