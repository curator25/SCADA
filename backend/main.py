import sys                                        # Exit handling
import signal                                     # Graceful shutdown on SIGINT/SIGTERM
import socket                                     # Check the API port before binding
import threading                                  # Stop event shared by the background threads
from pathlib import Path                          # Locate config.json next to this file

import uvicorn                                    # ASGI server for FastAPI


def _port_in_use(host, port):
    """Return True if something is already listening on host:port."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("" if host in ("0.0.0.0", "::") else host, int(port)))
        return False        # bind succeeded -> port is free
    except OSError:
        return True         # bind failed -> already in use
    finally:
        probe.close()

from app.logging_setup import setup_logging
from app.config import ConfigReloader, start_config_watcher
from app.modbus_client import ModbusReader
from app.latest import LatestStore
from app.poller import Poller
from app.db import Database
from app.writer import MinuteWriter
from app.api import create_app


def main():
    logger = setup_logging("kpi")

    # ---- configuration (hot-reloadable) ----
    config_path = Path(__file__).resolve().parent / "config.json"
    cfg = ConfigReloader(config_path, logger)
    start_config_watcher(cfg)

    # ---- shared state between threads ----
    stop_event = threading.Event()
    store = LatestStore()
    store.ensure_parameters([p.get("name") for p in cfg.parameters()])

    # ---- Modbus + database ----
    reader = ModbusReader(cfg, logger)
    reader.connect()            # First attempt; the poller retries on its own if this fails

    database = Database(cfg, logger)
    if cfg.get("database", "enabled", default=True):
        database.connect()      # Also retried later by the writer if it fails now

    # ---- background workers ----
    poller = Poller(reader, store, cfg, logger, stop_event)
    writer = MinuteWriter(store, database, cfg, logger, stop_event)
    poller.start()
    writer.start()

    # ---- shutdown handling ----
    def handle_exit(signum, frame):
        logger.info("Shutting down...")
        stop_event.set()
        reader.close()
        database.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)       # Ctrl+C
    try:
        signal.signal(signal.SIGTERM, handle_exit)  # Linux / container stop
    except AttributeError:
        pass                                        # Windows has no SIGTERM

    # ---- HTTP API (runs in the main thread) ----
    app = create_app(store, cfg, logger)
    host = cfg.get("api", "host", default="0.0.0.0")
    port = cfg.get("api", "port", default=8000)

    # Fail with a clear message if the port is taken (almost always a second
    # backend started while another - terminal or Docker - is still running),
    # instead of a confusing crash that looks like a Modbus problem.
    if _port_in_use(host, port):
        logger.error(
            f"API port {port} is already in use - another backend "
            f"(terminal or Docker) is probably still running. Stop it first. Exiting."
        )
        stop_event.set()
        reader.close()
        database.close()
        sys.exit(1)

    logger.info(f"API listening on http://{host}:{port}  (GET /api/kpi)")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
