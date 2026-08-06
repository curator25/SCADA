import time                                              # Monotonic clock for drift-free pacing
import threading                                         # Runs as a background thread


class Poller(threading.Thread):
    """Reads the data source every `read_interval_seconds` (1s) and updates LatestStore.

    Nothing here writes to the database - persistence is the MinuteWriter's job.
    That separation is what lets the HMI keep updating while the DB is down.

    The source is whatever `build_source_chain` produced: Modbus, a CSV file, a
    SQL table, or a priority chain that falls back between them. This loop only
    knows that `read()` returns named values or None - register decoding lives
    in ModbusSource, where it belongs.
    """

    def __init__(self, source, store, cfg, logger, stop_event):
        super().__init__(name="poller", daemon=True)
        self.source = source
        self.store = store
        self.cfg = cfg
        self.logger = logger
        self.stop_event = stop_event
        self._was_connected = None      # Track transitions so we log them only once

    def run(self):
        self.logger.info("Poller started")

        while not self.stop_event.is_set():
            cycle_start = time.monotonic()
            interval = self.cfg.get("polling", "read_interval_seconds", default=1)

            # An unexpected error must not kill the poller - if this thread dies
            # the panel silently freezes on its last values with no warning
            # anywhere, which is worse than a logged failure.
            try:
                decoded = self.source.read()

                if decoded is None:
                    self.store.mark_bad()
                    if self._was_connected is not False:
                        self.logger.warning("Source read failed - values marked bad")
                        self._was_connected = False
                else:
                    self.store.update(decoded)
                    if self._was_connected is not True:
                        self.logger.info("Source read OK - live values updating")
                        self._was_connected = True

            except Exception as e:
                self.store.mark_bad()
                self._was_connected = False
                self.logger.error(f"Poll cycle failed: {e}")

            # Sleep only for the time left in this cycle, so reads stay on a steady 1s cadence
            elapsed = time.monotonic() - cycle_start
            remaining = interval - elapsed
            if remaining > 0:
                self.stop_event.wait(remaining)
            else:
                self.logger.debug(f"Poll cycle overran by {-remaining:.3f}s")

        self.logger.info("Poller stopped")
