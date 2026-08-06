import threading                  # Guard the shared dictionary
from collections import deque     # Fixed-size fallback buffer of minute rows
from datetime import datetime     # Timestamp of the last successful read


class LatestStore:
    """Holds the most recent value of every parameter, in memory.

    This is the hand-off point between the 1-second poller (writer) and both
    the HTTP API and the minute writer (readers). The KPI panel polls the API
    every second and is served entirely from here - it never touches the
    database, so DB problems can never blank out the HMI.

    Computed KPIs (e.g. the Performance Ratio) are kept in a SEPARATE dict.
    That is deliberate: `mark_bad()` clears every live parameter when the
    Modbus link drops, but a PR computed five minutes ago is still perfectly
    valid. Sharing one dict would erase it on every one-second hiccup.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._values = {}           # name -> {"value": float|None, "quality": "good"|"bad"}
        self._kpis = {}             # name -> {"value","status","computed_at","stale"}
        self._updated_at = None     # When the last successful read happened
        self._connected = False     # Current Modbus link state

    # ------------------------------------------------------------------ #
    # live Modbus parameters
    # ------------------------------------------------------------------ #
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
        instead of a misleading 0.00. Computed KPIs are left untouched.
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

    # ------------------------------------------------------------------ #
    # computed KPIs
    # ------------------------------------------------------------------ #
    def publish_kpi(self, name, value, status, computed_at):
        """Store a freshly computed KPI value."""
        with self._lock:
            self._kpis[name] = {
                "value": value,
                "status": status,
                "computed_at": computed_at,
                "stale": False,
            }

    def hold_kpi(self, name, status):
        """A cycle produced no new value - keep the previous one, flag it stale.

        This is what makes the panel hold the last PR overnight instead of
        blanking: the number stays, but `stale` tells the UI it is not current.
        """
        with self._lock:
            existing = self._kpis.get(name)
            if existing is None:
                self._kpis[name] = {
                    "value": None,
                    "status": status,
                    "computed_at": None,
                    "stale": False,
                }
            else:
                existing["status"] = status
                existing["stale"] = True

    def snapshot(self):
        """Return a copy of the current values plus link metadata."""
        with self._lock:
            return {
                "values": {k: dict(v) for k, v in self._values.items()},
                "kpis": {k: dict(v) for k, v in self._kpis.items()},
                "updated_at": self._updated_at,
                "connected": self._connected,
            }


class MinuteBuffer:
    """The last N minute rows, kept in memory as a fallback for the KPI calc.

    The PR calculator normally reads its 5 minute rows back from the database,
    which is the single source of truth. But if the database is down those rows
    are sitting in the writer's retry spool instead, and PR would silently stop
    updating - breaking the rule that a DB outage must never affect the HMI.
    So the writer also drops every row in here, and the calculator falls back
    to it when the query returns nothing.
    """

    def __init__(self, maxlen=15):
        self._lock = threading.Lock()
        self._rows = deque(maxlen=maxlen)   # (timestamp, row dict)

    def add(self, ts, row):
        with self._lock:
            self._rows.append((ts, dict(row)))

    def window(self, start, end):
        """Rows with start < timestamp <= end, oldest first."""
        with self._lock:
            return [dict(row) for ts, row in self._rows if start < ts <= end]
