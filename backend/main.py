import sys                                        # Exit handling
import signal                                     # Graceful shutdown on SIGINT/SIGTERM
import socket                                     # Check the API port before binding
import threading                                  # Stop event shared by the background threads
from pathlib import Path                          # Locate config.json next to this file

import uvicorn                                    # ASGI server for FastAPI


def _port_in_use(host, port):
    """Return True if something is already serving on host:port.

    Two probes, because neither is sufficient on its own:

      connect - catches a listener that binding would miss. Docker Desktop on
                Windows publishes ports through a WSL relay, which does NOT
                stop another process binding the same port - so a bind-only
                check happily starts a second backend alongside the container,
                and both then poll the device and fight over the same rows.

      bind    - catches a socket that is held but refuses connections.
    """
    port = int(port)
    wildcard = host in ("0.0.0.0", "::", "")
    target = "127.0.0.1" if wildcard else host

    # 1) Is anything answering?
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.6)
    try:
        probe.connect((target, port))
        return True         # something is serving -> in use
    except OSError:
        pass                # nothing answered - fall through to the bind test
    finally:
        probe.close()

    # 2) Can we take the port ourselves?
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("" if wildcard else host, port))
        return False        # bind succeeded -> port is free
    except OSError:
        return True         # bind failed -> already in use
    finally:
        probe.close()

from app.logging_setup import setup_logging
from app.config import ConfigReloader, start_config_watcher
from app.latest import LatestStore, MinuteBuffer
from app.poller import Poller
from app.db import Database, validate_database
from app.writer import MinuteWriter
from app.kpi import PRCalculator, validate_kpi_config
from app.timeutil import validate_timezones
from app.sources import build_source_chain, validate_sources, ModbusSource
from app.api import create_app


def main():
    logger = setup_logging("kpi")

    # ---- configuration (hot-reloadable) ----
    config_path = Path(__file__).resolve().parent / "config.json"
    cfg = ConfigReloader(config_path, logger)
    start_config_watcher(cfg)

    host = cfg.get("api", "host", default="0.0.0.0")
    port = cfg.get("api", "port", default=8000)

    # ---- refuse to start a second backend, BEFORE touching anything ----
    #
    # This check has to come first. Run it later and the process has already
    # opened the Modbus connection, connected to the database and started the
    # worker threads - so a backend that was never going to run still polls the
    # device, writes rows, and collides with the one that owns the port. The
    # only safe moment to bail out is before any of that exists.
    if _port_in_use(host, port):
        logger.error(
            f"API port {port} is already in use - another backend (terminal or "
            f"Docker) is already running. Nothing was started; this process is "
            f"exiting so the two cannot fight over the device and the database."
        )
        logger.error(
            "Stop the other one first:  docker compose stop backend   "
            "(or close the other terminal), then start this one again."
        )
        sys.exit(1)

    # ---- configuration sanity ----
    # Report bad settings once, here, rather than failing quietly on every
    # cycle. A mistyped timezone in particular would otherwise fall back to UTC
    # in silence and shift every stored timestamp and every CSV by hours.
    validate_timezones(cfg, logger)
    validate_sources(cfg, logger)
    validate_database(cfg, logger)

    # ---- shared state between threads ----
    stop_event = threading.Event()
    store = LatestStore()
    store.ensure_parameters([p.get("name") for p in cfg.parameters()])

    # Last few minute rows, so the KPI calculator keeps working if the DB drops.
    # Sized from the KPI interval with headroom, so raising interval_minutes
    # cannot silently leave the fallback short of a full window.
    kpi_interval = cfg.get("kpi", "interval_minutes", default=5)
    if not isinstance(kpi_interval, int) or kpi_interval <= 0:
        kpi_interval = 5
    minute_buffer = MinuteBuffer(maxlen=max(15, kpi_interval * 3))

    # ---- data sources + database ----
    source = build_source_chain(cfg, logger)

    # First attempt only; the poller retries on its own if this fails, and the
    # chain falls back to the next source meanwhile.
    for candidate in source.sources:
        if isinstance(candidate, ModbusSource):
            candidate.connect()

    database = Database(cfg, logger)
    if cfg.get("database", "enabled", default=True):
        database.connect()      # Also retried later by the writer if it fails now

    # ---- background workers ----
    poller = Poller(source, store, cfg, logger, stop_event)
    writer = MinuteWriter(store, database, cfg, logger, stop_event, buffer=minute_buffer)
    poller.start()
    writer.start()

    if cfg.get("kpi", "enabled", default=True):
        # Report bad KPI settings once, here, instead of failing quietly on
        # every interval for the rest of the run.
        if validate_kpi_config(cfg, logger):
            logger.error("KPI configuration has problems - PR may not compute")

        calculator = PRCalculator(store, database, minute_buffer, cfg, logger, stop_event)
        calculator.start()

    # ---- shutdown handling ----
    def handle_exit(signum, frame):
        logger.info("Shutting down...")
        stop_event.set()
        source.close()
        database.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)       # Ctrl+C
    try:
        signal.signal(signal.SIGTERM, handle_exit)  # Linux / container stop
    except AttributeError:
        pass                                        # Windows has no SIGTERM

    # ---- HTTP API (runs in the main thread) ----
    # The port was checked before anything was started, but bind can still fail
    # if something grabbed it in between. Shut the workers down cleanly rather
    # than leaving them polling behind a dead API.
    app = create_app(store, cfg, logger, database, source)

    logger.info(f"API listening on http://{host}:{port}  (GET /api/kpi)")
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except OSError as e:
        logger.error(f"Could not bind {host}:{port} - {e}")
    finally:
        stop_event.set()
        source.close()
        database.close()


if __name__ == "__main__":
    main()
