import threading                  # Guard the shared dictionary
from datetime import datetime     # Timestamp of the last successful read


class LatestStore:
    """Holds the most recent value of every parameter, in memory.

    This is the hand-off point between the 1-second poller (writer) and both
    the HTTP API and the minute writer (readers). The KPI panel polls the API
    every second and is served entirely from here - it never touches the
    database, so DB problems can never blank out the HMI.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._values = {}           # name -> {"value": float|None, "quality": "good"|"bad"}
        self._updated_at = None     # When the last successful read happened
        self._connected = False     # Current Modbus link state

    def update(self, decoded):
        """Store a fresh set of decoded values from a successful read."""
        now = datetime.now()
        with self._lock:
            for name, value in decoded.items():
                self._values[name] = {
                    "value": value,
                    "quality": "good" if value is not None else "bad",
                }
            self._updated_at = now
            self._connected = True

    def mark_bad(self):
        """Flag every parameter as unreadable (e.g. Modbus link is down).

        Values are set to None rather than 0.0 so the panel can show '--'
        instead of a misleading 0.00.
        """
        with self._lock:
            for name in self._values:
                self._values[name] = {"value": None, "quality": "bad"}
            self._connected = False

    def ensure_parameters(self, names):
        """Make sure every configured parameter has an entry, even before the first read."""
        with self._lock:
            for name in names:
                self._values.setdefault(name, {"value": None, "quality": "bad"})

    def snapshot(self):
        """Return a copy of the current values plus link metadata."""
        with self._lock:
            return {
                "values": {k: dict(v) for k, v in self._values.items()},
                "updated_at": self._updated_at,
                "connected": self._connected,
            }
