import time                                              # Monotonic clock for drift-free pacing
import threading                                         # Runs as a background thread
from .modbus_client import decode_parameters             # Raw registers -> named values


class Poller(threading.Thread):
    """Reads the Modbus block every `read_interval_seconds` (1s) and updates LatestStore.

    Nothing here writes to the database - persistence is the MinuteWriter's job.
    That separation is what lets the HMI keep updating while the DB is down.
    """

    def __init__(self, reader, store, cfg, logger, stop_event):
        super().__init__(name="poller", daemon=True)
        self.reader = reader
        self.store = store
        self.cfg = cfg
        self.logger = logger
        self.stop_event = stop_event
        self._was_connected = None      # Track transitions so we log them only once

    def run(self):
        self.logger.info("Poller started")

        while not self.stop_event.is_set():
            cycle_start = time.monotonic()

            # Re-read these each cycle so a config hot-reload takes effect immediately
            interval = self.cfg.get("polling", "read_interval_seconds", default=1)
            register_start = self.cfg.get("modbus", "register_start")
            word_order = self.cfg.get("modbus", "word_order", default="big")
            byte_order = self.cfg.get("modbus", "byte_order", default="big")
            parameters = self.cfg.parameters()

            registers = self.reader.read_block()

            if registers is None:
                self.store.mark_bad()
                if self._was_connected is not False:
                    self.logger.warning("Modbus read failed - values marked bad")
                    self._was_connected = False
            else:
                decoded = decode_parameters(registers, parameters, register_start, word_order, byte_order)
                self.store.update(decoded)
                if self._was_connected is not True:
                    self.logger.info("Modbus read OK - live values updating")
                    self._was_connected = True

            # Sleep only for the time left in this cycle, so reads stay on a steady 1s cadence
            elapsed = time.monotonic() - cycle_start
            remaining = interval - elapsed
            if remaining > 0:
                self.stop_event.wait(remaining)
            else:
                self.logger.debug(f"Poll cycle overran by {-remaining:.3f}s")

        self.logger.info("Poller stopped")
