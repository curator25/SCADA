import os            # KPI_DB_URL must keep overriding everything, as in Phase 1

from sqlalchemy import (create_engine, MetaData, Table, Column, DateTime, String, Float,
                        insert, select, inspect, text,)
from sqlalchemy.exc import IntegrityError

READINGS = "readings"   # kpi_readings - one wide row per minute
METRICS = "metrics"     # kpi_metrics  - one wide row per KPI interval

# Storage backends that may be selected with a true/false switch. SQLAlchemy
# handles the dialect difference, so the URL is the only thing that changes.
DESTINATIONS = ("mysql", "postgres")


def validate_database(cfg, logger):
    """Check the storage settings once at startup. Returns the problems."""
    problems = []

    enabled = [d for d in DESTINATIONS if cfg.get("database", d, "enabled", default=False)]

    if len(enabled) > 1:
        problems.append(
            f"database.{' and database.'.join(enabled)} are both enabled - "
            f"enable exactly one. Writing to two databases at once is not "
            f"supported (it needs two engines and two retry spools)."
        )

    for name in enabled:
        if not cfg.get("database", name, "url"):
            problems.append(f"database.{name} is enabled but its 'url' is not set")

    if not enabled and not cfg.get("database", "url"):
        problems.append(
            "no storage destination - set database.url, or enable "
            "database.mysql or database.postgres with a url"
        )

    for problem in problems:
        logger.error(f"Database config: {problem}")

    return problems

# Outcomes of an insert. The split matters: only INSERT_FAILED is worth
# retrying. A duplicate primary key can never succeed on a retry, so treating
# it as a transient failure would put a row in the spool that jams it forever.
INSERT_OK = "ok"
INSERT_DUPLICATE = "duplicate"
INSERT_FAILED = "failed"


class Database:
    """Owns the engine and both tables.

    Phase 1 had a single table, so this class held one `self.table`. Phase 2
    adds the computed-KPI table, so tables are keyed in a dict instead - the
    connect/create/ALTER logic is shared between them.
    """

    def __init__(self, cfg, logger):
        self.cfg = cfg
        self.logger = logger
        self.engine = None
        self.tables = {}        # key -> sqlalchemy Table

    # ------------------------------------------------------------------ #
    # table definitions
    # ------------------------------------------------------------------ #
    def _build_readings_table(self, metadata):
        """Timestamp + Plant Name + one column per configured parameter."""
        table_name = self.cfg.get("database", "table", default="kpi_readings")
        ts_col = self.cfg.get("database", "timestamp_column", default="Timestamp")
        plant_col = self.cfg.get("database", "plant_column", default="Plant Name")

        columns = [
            Column(ts_col, DateTime, primary_key=True, nullable=False),
            Column(plant_col, String(64), primary_key=True, nullable=False),
        ]

        # One DOUBLE column per configured parameter, named by its literal label.
        # Hidden parameters (display:false) are stored exactly like visible ones -
        # they are inputs to the KPI maths, so their history matters.
        for param in self.cfg.parameters():
            column_name = param.get("column") or param.get("name")
            columns.append(Column(column_name, Float, nullable=True))
        return Table(table_name, metadata, *columns)

    def _build_metrics_table(self, metadata):
        """Timestamp + one column per computed KPI.

        Same shape as kpi_readings, and driven by config the same way: the
        columns come from the `kpi.metrics` list, so adding the next KPI is a
        config edit and _sync_columns ALTERs the table in on the next start.
        """
        table_name = self.cfg.get("kpi", "table", default="kpi_metrics")
        ts_col = self.cfg.get("database", "timestamp_column", default="Timestamp")

        columns = [Column(ts_col, DateTime, primary_key=True, nullable=False)]

        for metric in self.cfg.get("kpi", "metrics", default=[]) or []:
            column_name = metric.get("column") or metric.get("name")
            columns.append(Column(column_name, Float, nullable=True))

        return Table(table_name, metadata, *columns)

    # ------------------------------------------------------------------ #
    # connection / schema
    # ------------------------------------------------------------------ #
    def _sync_columns(self, table):
        """Add columns for parameters that were added to config after table creation."""
        inspector = inspect(self.engine)
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        preparer = self.engine.dialect.identifier_preparer

        missing = [c for c in table.columns if c.name not in existing]
        if not missing:
            return

        with self.engine.begin() as conn:
            for column in missing:
                column_type = column.type.compile(self.engine.dialect)
                ddl = (
                    f"ALTER TABLE {preparer.quote(table.name)} "
                    f"ADD COLUMN {preparer.quote(column.name)} {column_type} NULL"
                )
                conn.execute(text(ddl))
                self.logger.warning(f"Added missing column '{column.name}' to '{table.name}'")

    def _resolve_url(self):
        """(url, where it came from) for the storage destination.

        Order matters. KPI_DB_URL is checked first because docker-compose sets
        it to reach the host database, and that override has to keep winning
        regardless of which destination block is switched on. After that an
        explicitly enabled mysql/postgres block wins, and finally the plain
        `database.url` from Phase 1 - so an existing config needs no edit.
        """
        env_url = os.getenv("KPI_DB_URL")
        if env_url:
            return env_url, "KPI_DB_URL"

        for name in DESTINATIONS:
            if self.cfg.get("database", name, "enabled", default=False):
                url = self.cfg.get("database", name, "url")
                if url:
                    return url, f"database.{name}"

        return self.cfg.get("database", "url"), "database.url"

    def connect(self):
        """Create the engine, create both tables if missing, add any new columns."""
        url, origin = self._resolve_url()

        # pool_pre_ping lets SQLAlchemy detect dropped connections and reconnect
        try:
            self.engine = create_engine(url, pool_pre_ping=True, future=True)

            metadata = MetaData()
            self.tables = {
                READINGS: self._build_readings_table(metadata),
                METRICS: self._build_metrics_table(metadata),
            }

            metadata.create_all(self.engine)        # CREATE TABLE IF NOT EXISTS
            for table in self.tables.values():
                self._sync_columns(table)           # ALTER TABLE for new parameters

            names = ", ".join(f"'{t.name}'" for t in self.tables.values())
            self.logger.info(
                f"Database connected via {origin} "
                f"({self.engine.dialect.name}, tables {names})"
            )
            return True

        except Exception as e:
            self.logger.error(f"Database connection failed: {e}")
            self.engine = None
            return False

    # ------------------------------------------------------------------ #
    # writes
    # ------------------------------------------------------------------ #
    def insert_row(self, row, table_key=READINGS):
        """Insert one row. Returns INSERT_OK / INSERT_DUPLICATE / INSERT_FAILED."""
        if not row:
            return INSERT_OK

        if self.engine is None and not self.connect():
            return INSERT_FAILED

        table = self.tables[table_key]

        try:
            with self.engine.begin() as conn:   # commits on success, rolls back on error
                conn.execute(insert(table), [row])
            return INSERT_OK

        except IntegrityError:
            # The row is already there. Usually a second backend writing the
            # same plant (a local run alongside the container), or a restart
            # inside the same minute. The connection is fine, so don't drop the
            # engine, and don't queue the row - a retry can only fail again.
            ts_col = self.cfg.get("database", "timestamp_column", default="Timestamp")
            self.logger.warning(
                f"Duplicate row in '{table.name}' at {row.get(ts_col)} - already "
                f"stored, skipped. Normal right after a restart; if it repeats "
                f"every interval, a second backend is writing to this database."
            )
            return INSERT_DUPLICATE

        except Exception as e:
            self.logger.error(f"Database insert failed ({table_key}): {e}")
            self.engine = None      # Force a reconnect on the next attempt
            return INSERT_FAILED

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #
    def fetch_window(self, start, end, plant):
        """Minute rows with start < Timestamp <= end, oldest first.

        Returns None (not []) when the database is unreachable, so the caller
        can tell "DB down, use the fallback buffer" apart from "DB fine, but
        there genuinely are no rows in this window".
        """
        if self.engine is None and not self.connect():
            return None

        try:
            table = self.tables[READINGS]
            ts_col = self.cfg.get("database", "timestamp_column", default="Timestamp")
            plant_col = self.cfg.get("database", "plant_column", default="Plant Name")

            stmt = (
                select(table)
                .where(table.c[ts_col] > start)
                .where(table.c[ts_col] <= end)
                .where(table.c[plant_col] == plant)
                .order_by(table.c[ts_col])
            )

            with self.engine.connect() as conn:
                return [dict(r._mapping) for r in conn.execute(stmt)]

        except Exception as e:
            self.logger.error(f"Database read failed: {e}")
            self.engine = None      # Force a reconnect on the next attempt
            return None

    def fetch_metrics(self, start=None, end=None):
        """Computed KPI rows, oldest first, for the reports.

        Bounds are optional and inclusive; None means "no limit on that side".
        Returns None when the database is unreachable.
        """
        if self.engine is None and not self.connect():
            return None

        try:
            table = self.tables[METRICS]
            ts_col = self.cfg.get("database", "timestamp_column", default="Timestamp")

            stmt = select(table).order_by(table.c[ts_col])
            if start is not None:
                stmt = stmt.where(table.c[ts_col] >= start)
            if end is not None:
                stmt = stmt.where(table.c[ts_col] <= end)

            with self.engine.connect() as conn:
                return [dict(r._mapping) for r in conn.execute(stmt)]

        except Exception as e:
            self.logger.error(f"Database read failed (metrics): {e}")
            self.engine = None      # Force a reconnect on the next attempt
            return None

    def close(self):
        if self.engine is not None:
            try:
                self.engine.dispose()
            except Exception:
                pass
            self.engine = None
