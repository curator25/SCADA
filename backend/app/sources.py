import os                                          # Tail files without reading them whole
import io                                          # Parse single CSV lines
import csv                                         # Delimited file parsing
import time                                        # Monotonic clock for failback timing
import threading                                   # The chain is read from the poller thread
from datetime import datetime, timedelta           # Staleness checks

from sqlalchemy import create_engine, MetaData, Table, select

from .modbus_client import ModbusReader, decode_parameters
from .timeutil import resolve_timezone

# --------------------------------------------------------------------------- #
# Where live values come from.
#
# Phase 1 could only read a Modbus TCP device. Different plants publish their
# data differently - some write a CSV that another system produces, some already
# log into MySQL or Postgres - so the source is now pluggable and chosen in
# config.json with plain true/false switches.
#
# Every source returns ALREADY-NAMED values ({parameter_name: value}), not raw
# registers. That is what lets the poller stop caring which kind it is talking
# to: register decoding is now purely a Modbus concern.
#
# A source returns None to mean "no reading this cycle", which triggers
# fallback. Sources must never raise into the poll loop.
# --------------------------------------------------------------------------- #


class DataSource:
    """Common interface. `read()` returns {parameter_name: value} or None."""

    name = "source"

    def read(self):
        raise NotImplementedError

    def close(self):
        pass


# --------------------------------------------------------------------------- #
# Modbus TCP
# --------------------------------------------------------------------------- #
class ModbusSource(DataSource):
    """The Phase 1 path, wrapped.

    ModbusReader and decode_parameters are used exactly as before - this only
    moves the "registers -> named values" step out of the poller.
    """

    name = "modbus"

    def __init__(self, cfg, logger):
        self.cfg = cfg
        self.logger = logger
        self.reader = ModbusReader(cfg, logger)

    def connect(self):
        return self.reader.connect()

    def read(self):
        registers = self.reader.read_block()
        if registers is None:
            return None

        # Re-read each cycle so a config hot-reload takes effect immediately
        return decode_parameters(
            registers,
            self.cfg.parameters(),
            self.cfg.get("modbus", "register_start"),
            self.cfg.get("modbus", "word_order", default="big"),
            self.cfg.get("modbus", "byte_order", default="big"),
        )

    def close(self):
        self.reader.close()


# --------------------------------------------------------------------------- #
# Shared helpers for the non-Modbus sources
# --------------------------------------------------------------------------- #
def _column_map(cfg, match_by):
    """{header name -> parameter name} for mapping incoming columns.

    `match_by` picks which side of the parameter definition the incoming data
    uses: "column" for the human-readable DB names ("POA Avg"), "name" for the
    internal keys ("poa_avg").
    """
    mapping = {}
    for param in cfg.parameters():
        name = param.get("name")
        key = param.get("column") or name if match_by == "column" else name
        if key:
            mapping[str(key).strip()] = name
    return mapping


def _to_float(text):
    """Parse a value, returning None rather than raising on junk or blanks."""
    if text is None:
        return None
    text = str(text).strip()
    if text == "" or text.upper() in ("NULL", "NA", "N/A", "NAN"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
)


def _parse_timestamp(text):
    """Best-effort timestamp parsing across the usual plant export formats."""
    if isinstance(text, datetime):
        return text
    if text is None:
        return None

    text = str(text).strip()
    if not text:
        return None

    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass

    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


class _StaleCheck:
    """Shared 'is this reading actually current?' logic.

    This matters more than it looks. A file or table that stops being updated
    still READS perfectly well - the newest row is simply old. Without an age
    check the system would serve last week's irradiance as live data and never
    fall back to a working source.
    """

    def __init__(self, logger, label):
        self.logger = logger
        self.label = label
        self._warned = False

    def is_stale(self, stamp, max_age_seconds, now):
        if not max_age_seconds or max_age_seconds <= 0:
            return False                        # Check disabled on purpose

        if stamp is None:
            if not self._warned:
                self._warned = True
                self.logger.warning(
                    f"{self.label}: no usable timestamp, cannot tell whether the "
                    f"data is current - treating it as stale"
                )
            return True

        age = (now - stamp).total_seconds()
        if age > max_age_seconds:
            if not self._warned:
                self._warned = True
                self.logger.warning(
                    f"{self.label}: newest reading is {age:.0f}s old "
                    f"(limit {max_age_seconds}s) - treating as stale"
                )
            return True

        self._warned = False                    # Healthy again; warn next time too
        return False


# --------------------------------------------------------------------------- #
# CSV / TXT file
# --------------------------------------------------------------------------- #
class FileSource(DataSource):
    """Newest row of a delimited file whose header names the parameters.

    Values are taken as-is: unlike Modbus registers they are already in
    engineering units, so the parameter `scale` is deliberately NOT applied.
    """

    name = "file"

    def __init__(self, cfg, logger, section="file_source"):
        self.cfg = cfg
        self.logger = logger
        self.section = section
        self._stale = _StaleCheck(logger, f"{section}")
        self._missing_logged = False

    def _tz(self):
        return resolve_timezone(self.cfg.get("timezone", default="UTC"), self.logger)

    def _header_and_last_row(self, path, encoding):
        """(header line, last COMPLETE data line) without reading the whole file.

        Reads only the first line and a tail block, so a file that grows all day
        does not cost more every second.
        """
        with open(path, "rb") as handle:
            header = handle.readline()
            if not header:
                return None, None

            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            header_len = len(header)
            if size <= header_len:
                return header.decode(encoding, "replace"), None   # header only

            block = min(64 * 1024, size - header_len)
            handle.seek(size - block)
            tail = handle.read(block)

        # A producer may be midway through appending. If the file does not end
        # with a newline the final line is partial, so use the one before it -
        # half a row would otherwise parse into wrong or missing values.
        complete = tail.endswith(b"\n") or tail.endswith(b"\r")
        lines = [ln for ln in tail.decode(encoding, "replace").splitlines() if ln.strip()]
        if not complete and lines:
            lines.pop()

        return header.decode(encoding, "replace"), (lines[-1] if lines else None)

    def read(self):
        path = self.cfg.get(self.section, "path")
        if not path:
            return None

        delimiter = self.cfg.get(self.section, "delimiter", default=",") or ","
        encoding = self.cfg.get(self.section, "encoding", default="utf-8-sig")
        match_by = self.cfg.get(self.section, "match_by", default="column")
        ts_column = self.cfg.get(self.section, "timestamp_column", default="Timestamp")
        max_age = self.cfg.get(self.section, "max_age_seconds", default=300)

        try:
            header_line, row_line = self._header_and_last_row(path, encoding)
        except FileNotFoundError:
            if not self._missing_logged:
                self._missing_logged = True
                self.logger.warning(f"{self.section}: file not found: {path}")
            return None
        except OSError as e:
            # Locked by the writing process, permissions, network share down...
            if not self._missing_logged:
                self._missing_logged = True
                self.logger.warning(f"{self.section}: cannot read {path}: {e}")
            return None

        self._missing_logged = False

        if not header_line or not row_line:
            return None

        try:
            headers = next(csv.reader(io.StringIO(header_line), delimiter=delimiter))
            values = next(csv.reader(io.StringIO(row_line), delimiter=delimiter))
        except (StopIteration, csv.Error):
            return None

        row = dict(zip((h.strip() for h in headers), values))

        # Only the header row present, or the tail happened to re-read it
        if not row or row_line.strip() == header_line.strip():
            return None

        now = datetime.now(self._tz()).replace(tzinfo=None)
        if self._stale.is_stale(_parse_timestamp(row.get(ts_column)), max_age, now):
            return None

        mapping = _column_map(self.cfg, match_by)
        decoded = {}
        for header, param_name in mapping.items():
            decoded[param_name] = _to_float(row.get(header))

        # Every configured parameter missing means the header does not match at
        # all - report failure so the chain falls back instead of publishing a
        # full set of nulls that looks like a dead sensor.
        if decoded and all(v is None for v in decoded.values()):
            return None

        return decoded or None

    def close(self):
        pass


# --------------------------------------------------------------------------- #
# MySQL / Postgres
# --------------------------------------------------------------------------- #
class SqlSource(DataSource):
    """Newest row of a table in an existing plant database.

    One class serves both MySQL and Postgres - the URL selects the driver, so
    the only difference between the two config blocks is that string.
    """

    def __init__(self, cfg, logger, section):
        self.cfg = cfg
        self.logger = logger
        self.section = section
        self.name = section.replace("_source", "")
        self.engine = None
        self.table = None
        self._stale = _StaleCheck(logger, section)
        self._error_logged = False

    def _tz(self):
        return resolve_timezone(self.cfg.get("timezone", default="UTC"), self.logger)

    def _connect(self):
        url = self.cfg.get(self.section, "url")
        table_name = self.cfg.get(self.section, "table")
        if not url or not table_name:
            return False

        try:
            self.engine = create_engine(url, pool_pre_ping=True, future=True)
            metadata = MetaData()
            # Reflect once at connect rather than per read - the shape of a
            # plant's table is not going to change between polls.
            self.table = Table(table_name, metadata, autoload_with=self.engine)
            self.logger.info(f"{self.section}: connected (table '{table_name}')")
            self._error_logged = False
            return True
        except Exception as e:
            if not self._error_logged:
                self._error_logged = True
                self.logger.warning(f"{self.section}: connection failed: {e}")
            self.engine = None
            self.table = None
            return False

    def read(self):
        if self.engine is None and not self._connect():
            return None

        ts_column = self.cfg.get(self.section, "timestamp_column", default="Timestamp")
        match_by = self.cfg.get(self.section, "match_by", default="column")
        max_age = self.cfg.get(self.section, "max_age_seconds", default=300)

        try:
            if ts_column in self.table.c:
                stmt = select(self.table).order_by(self.table.c[ts_column].desc()).limit(1)
            else:
                stmt = select(self.table).limit(1)

            with self.engine.connect() as conn:
                result = conn.execute(stmt).fetchone()

        except Exception as e:
            if not self._error_logged:
                self._error_logged = True
                self.logger.warning(f"{self.section}: read failed: {e}")
            self.engine = None      # Force a reconnect next cycle
            self.table = None
            return None

        self._error_logged = False
        if result is None:
            return None

        row = dict(result._mapping)

        now = datetime.now(self._tz()).replace(tzinfo=None)
        if self._stale.is_stale(_parse_timestamp(row.get(ts_column)), max_age, now):
            return None

        mapping = _column_map(self.cfg, match_by)
        decoded = {}
        for column, param_name in mapping.items():
            decoded[param_name] = _to_float(row.get(column))

        if decoded and all(v is None for v in decoded.values()):
            return None

        return decoded or None

    def close(self):
        if self.engine is not None:
            try:
                self.engine.dispose()
            except Exception:
                pass
        self.engine = None
        self.table = None


# --------------------------------------------------------------------------- #
# Priority chain with failover and failback
# --------------------------------------------------------------------------- #
class SourceChain(DataSource):
    """Tries the enabled sources in priority order and remembers what worked.

    Two behaviours matter here:

    Sticky      the currently working source is tried FIRST, so the steady
                state is one read per cycle. Walking the whole list every
                second would blow the 1s poll budget as soon as a source with
                a timeout is unreachable.

    Failback    higher-priority sources are re-probed every
                `failback_check_seconds`, so recovering the primary does not
                need a restart.
    """

    name = "chain"

    def __init__(self, sources, cfg, logger):
        self.sources = list(sources)
        self.cfg = cfg
        self.logger = logger
        self._lock = threading.Lock()
        self._active = self.sources[0] if self.sources else None
        self._last_failback = time.monotonic()
        self._all_failed = False

    @property
    def active_name(self):
        with self._lock:
            return self._active.name if self._active else None

    def _attempt_order(self, now, interval):
        """Which sources to try this cycle, in order."""
        if not self.sources:
            return []

        with self._lock:
            active = self._active

        if active is None or active not in self.sources:
            return list(self.sources)

        index = self.sources.index(active)
        if index > 0 and (now - self._last_failback) >= interval:
            self._last_failback = now
            return list(self.sources)       # Time to see if the primary is back

        return [active] + [s for s in self.sources if s is not active]

    def read(self):
        if not self.sources:
            return None

        now = time.monotonic()
        interval = self.cfg.get("sources", "failback_check_seconds", default=60)

        for source in self._attempt_order(now, interval):
            try:
                values = source.read()
            except Exception as e:
                # A source must never take the poller down with it
                self.logger.error(f"Source '{source.name}' raised: {e}")
                values = None

            if values is None:
                continue

            with self._lock:
                previous = self._active
                self._active = source

            if previous is not source or self._all_failed:
                self.logger.info(f"Reading from source '{source.name}'")
            self._all_failed = False
            return values

        if not self._all_failed:
            names = ", ".join(s.name for s in self.sources)
            self.logger.warning(f"No data source available (tried: {names})")
            self._all_failed = True

        return None

    def close(self):
        for source in self.sources:
            try:
                source.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Building and validating from config
# --------------------------------------------------------------------------- #
_SOURCE_SECTIONS = (
    # (config section, default enabled, default priority, factory)
    ("modbus", True, 1, lambda cfg, log, sec: ModbusSource(cfg, log)),
    ("file_source", False, 2, lambda cfg, log, sec: FileSource(cfg, log, sec)),
    ("mysql_source", False, 3, lambda cfg, log, sec: SqlSource(cfg, log, sec)),
    ("postgres_source", False, 4, lambda cfg, log, sec: SqlSource(cfg, log, sec)),
)


def build_source_chain(cfg, logger):
    """Create the enabled sources, ordered by priority.

    `modbus.enabled` defaults to True, so a config written before this feature
    existed still runs Modbus-only exactly as it did.
    """
    entries = []
    for section, default_enabled, default_priority, factory in _SOURCE_SECTIONS:
        if not cfg.get(section, "enabled", default=default_enabled):
            continue
        priority = cfg.get(section, "priority", default=default_priority)
        if not isinstance(priority, (int, float)):
            priority = default_priority
        entries.append((priority, section, factory(cfg, logger, section)))

    entries.sort(key=lambda item: item[0])

    if entries:
        order = " -> ".join(f"{s}(p{p})" for p, s, _ in entries)
        logger.info(f"Data sources in priority order: {order}")

    return SourceChain([source for _, _, source in entries], cfg, logger)


def validate_sources(cfg, logger):
    """Report bad source settings once at startup. Returns the problems."""
    problems = []
    enabled = []

    for section, default_enabled, _, _ in _SOURCE_SECTIONS:
        if cfg.get(section, "enabled", default=default_enabled):
            enabled.append(section)

    if not enabled:
        problems.append(
            "no data source is enabled - set 'enabled': true on modbus, "
            "file_source, mysql_source or postgres_source"
        )

    if "file_source" in enabled and not cfg.get("file_source", "path"):
        problems.append("file_source is enabled but 'path' is not set")

    for section in ("mysql_source", "postgres_source"):
        if section not in enabled:
            continue
        if not cfg.get(section, "url"):
            problems.append(f"{section} is enabled but 'url' is not set")
        if not cfg.get(section, "table"):
            problems.append(f"{section} is enabled but 'table' is not set")

    # Two sources on the same priority would make the fallback order depend on
    # dict ordering rather than on anything the operator chose.
    priorities = {}
    for section in enabled:
        default_priority = next(p for s, _, p, _ in _SOURCE_SECTIONS if s == section)
        priority = cfg.get(section, "priority", default=default_priority)
        priorities.setdefault(priority, []).append(section)

    for priority, sections in priorities.items():
        if len(sections) > 1:
            problems.append(
                f"sources {', '.join(sections)} share priority {priority} - "
                f"give each a distinct value so the fallback order is defined"
            )

    for problem in problems:
        logger.error(f"Source config: {problem}")

    return problems
