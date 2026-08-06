import json                          # Persist the retry queue to disk
import pickle                        # Measure the retry queue's real size in bytes
import threading                     # Runs as a background thread
from pathlib import Path             # Locate the spool file
from collections import deque        # Retry buffer for when the database is down
from datetime import datetime, timedelta   # Minute timestamps

from .db import INSERT_FAILED        # Only transient failures are worth retrying
from .timeutil import resolve_timezone


class MinuteWriter(threading.Thread):
    """Writes ONE wide row per minute, aligned to HH:MM:00 in UTC.

    It does not average the 60 samples - it stores the value that was live at
    the minute boundary, which is what was asked for.

    If the database is unreachable the row goes into a size-capped retry queue
    and is flushed on the next successful write, so a DB outage costs history
    but never stops the Modbus polling or the HMI.

    Each queued row carries an attempt count. A row that keeps failing for a
    reason a retry cannot fix is eventually dropped, because the flush stops at
    the first failure - so one poisoned row would otherwise block every row
    behind it forever.
    """

    def __init__(self, store, database, cfg, logger, stop_event, buffer=None):
        super().__init__(name="minute-writer", daemon=True)
        self.store = store
        self.db = database
        self.cfg = cfg
        self.logger = logger
        self.stop_event = stop_event
        self.buffer = buffer        # Shared MinuteBuffer the KPI calc falls back to
        self.queue = deque()        # (timestamp, row, attempts) that failed to insert

        # Disk-backed spool so buffered rows survive a crash/restart while the
        # DB is down. In Docker the logs/ dir is volume-mounted, so it persists
        # across container restarts too.
        self._spool = Path(__file__).resolve().parent.parent / "logs" / "pending_writes.json"
        self._load_queue()

    # ------------------------------------------------------------------ #
    # durable spool
    # ------------------------------------------------------------------ #
    def _persist_queue(self):
        """Write the current queue to disk atomically (temp file + rename)."""
        try:
            data = [
                {
                    "ts": ts.isoformat(),
                    "attempts": attempts,
                    "row": {
                        k: (v.isoformat() if isinstance(v, datetime) else v)
                        for k, v in row.items()
                    },
                }
                for ts, row, attempts in self.queue
            ]
            self._spool.parent.mkdir(exist_ok=True)
            tmp = self._spool.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(self._spool)                 # Atomic: no half-written file
        except Exception as e:
            self.logger.error(f"Could not persist retry queue: {e}")

    def _load_queue(self):
        """Reload any rows buffered before the last restart."""
        if not self._spool.exists():
            return
        try:
            ts_col = self.cfg.get("database", "timestamp_column", default="Timestamp")
            for item in json.loads(self._spool.read_text(encoding="utf-8")):
                ts = datetime.fromisoformat(item["ts"])
                row = item["row"]
                if isinstance(row.get(ts_col), str):     # Restore the datetime column
                    row[ts_col] = datetime.fromisoformat(row[ts_col])
                # Spools written before the retry cap existed have no attempts
                self.queue.append((ts, row, int(item.get("attempts", 0))))
            if self.queue:
                self.logger.info(f"Recovered {len(self.queue)} pending write(s) from spool")
        except Exception as e:
            self.logger.error(f"Could not load retry queue spool: {e}")
            self.queue.clear()      # A corrupt spool must not wedge the writer

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _tz(self):
        """Timezone the stored timestamps are written in (config `timezone`)."""
        return resolve_timezone(self.cfg.get("timezone", default="UTC"), self.logger)

    def _queue_size_bytes(self):
        """Approximate memory footprint of the retry queue."""
        try:
            return len(pickle.dumps(self.queue))
        except Exception:
            return 0

    def _enqueue(self, ts, row):
        """Buffer a failed row, dropping the oldest if we exceed the cap."""
        max_bytes = self.cfg.get("database", "queue_max_bytes", default=10 * 1024 * 1024)
        size = self._queue_size_bytes()

        if size < max_bytes:
            self.queue.append((ts, row, 0))
            self.logger.warning(
                f"DB unavailable. Queuing data. Queue size: {self._queue_size_bytes() / 1024:.2f} KB"
            )
        else:
            self.queue.popleft()                # Drop the oldest to make room
            self.queue.append((ts, row, 0))
            self.logger.error("Queue full. Dropped oldest data to add new one.")

        self._persist_queue()                   # Save so a restart doesn't lose it

    def _flush_queue(self):
        """Re-send everything buffered while the DB was down, oldest first."""
        if not self.queue:
            return

        max_attempts = self.cfg.get("database", "max_retry_attempts", default=10)

        self.logger.info(
            f"Processing queued data. Queue size: {self._queue_size_bytes() / 1024:.2f} KB"
        )
        self.queue = deque(sorted(self.queue, key=lambda item: item[0]))   # Oldest first
        remaining = deque()

        while self.queue:
            ts, row, attempts = self.queue.popleft()
            self.logger.info(f"Attempting to resend queued data from {ts}")

            # A duplicate is dropped rather than kept: the row already exists,
            # so holding on to it would block the queue permanently.
            if self.db.insert_row(row) != INSERT_FAILED:
                continue

            attempts += 1

            if attempts >= max_attempts:
                # Not a transient problem. Drop this one row and keep draining,
                # rather than letting it block every row behind it forever.
                self.logger.error(
                    f"Dropping queued row from {ts} after {attempts} failed attempts"
                )
                continue

            # Still failing - keep this and everything after it for the next attempt
            self.logger.warning(f"Failed to send queued data from {ts}. Keeping in queue.")
            remaining.append((ts, row, attempts))
            remaining.extend(self.queue)
            self.queue.clear()
            break

        self.queue = remaining
        self._persist_queue()                   # Reflect what's left (or clear the spool)
        self.logger.info(
            f"Queue processing complete. Remaining size: {self._queue_size_bytes() / 1024:.2f} KB"
        )

    def _build_row(self, ts):
        """Turn the current live snapshot into one wide DB row."""
        snapshot = self.store.snapshot()

        ts_col = self.cfg.get("database", "timestamp_column", default="Timestamp")
        plant_col = self.cfg.get("database", "plant_column", default="Plant Name")

        row = {
            ts_col: ts,
            plant_col: self.cfg.get("plant", "name", default="UNKNOWN"),
        }

        # Each parameter's value goes into its own column, keyed by the literal label
        for param in self.cfg.parameters():
            column_name = param.get("column") or param.get("name")
            entry = snapshot["values"].get(param.get("name"), {"value": None, "quality": "bad"})
            row[column_name] = entry["value"]       # None -> NULL when unreadable

        return row

    # ------------------------------------------------------------------ #
    # main loop
    # ------------------------------------------------------------------ #
    def _store_once(self, ts):
        """Build and store the row for boundary `ts` (naive, in the stored timezone)."""
        row = self._build_row(ts)

        # Keep a copy in memory regardless of what the database does, so the
        # KPI calculator can still work during a DB outage.
        if self.buffer is not None:
            self.buffer.add(ts, row)

        # Queue only on a transient failure. A duplicate means the row is
        # already stored, so there is nothing to retry.
        if self.db.insert_row(row) == INSERT_FAILED:
            self._enqueue(ts, row)
        else:
            self._flush_queue()                     # DB healthy - drain any backlog
            self.logger.info(f"Stored reading at {ts:%Y-%m-%d %H:%M:%S} UTC")

    def run(self):
        self.logger.info("Minute writer started")

        while not self.stop_event.is_set():
            interval = self.cfg.get("polling", "store_interval_seconds", default=60)

            # Sleep until the next exact boundary (e.g. 08:54:00, 08:55:00, ...)
            now = datetime.now(self._tz())
            seconds_into = (now.minute * 60 + now.second) % interval
            sleep_for = interval - seconds_into - now.microsecond / 1e6
            if sleep_for <= 0:
                sleep_for += interval

            # Work out the boundary we are aiming for BEFORE sleeping, and stamp
            # the row with it.
            #
            # Reading the clock after waking is not safe: wait() can return a
            # fraction of a millisecond early, and truncating 10:33:59.9998
            # yields 10:33 - which rewrites the previous minute as a duplicate
            # and loses the current one entirely. That silently punches a hole
            # in the KPI window (5 samples becomes 4).
            #
            # The +0.5s rounds to the nearest second instead of truncating, so
            # floating-point drift in sleep_for cannot land us a microsecond
            # short of the boundary either.
            target = (now + timedelta(seconds=sleep_for + 0.5)).replace(microsecond=0)
            if interval >= 60:
                target = target.replace(second=0)
            target = target.replace(tzinfo=None)

            if self.stop_event.wait(sleep_for):
                break                                   # Shutting down

            if not self.cfg.get("database", "enabled", default=True):
                continue                                # Storage switched off in config

            # An unexpected error here must not kill the thread - losing the
            # writer silently would stop all history with the HMI still looking
            # perfectly healthy.
            try:
                self._store_once(target)
            except Exception as e:
                self.logger.error(f"Minute writer cycle failed: {e}")

        self.logger.info("Minute writer stopped")
