from sqlalchemy import (create_engine, MetaData, Table, Column, DateTime, String, Float, insert, inspect, text,)

class Database:
    def __init__(self, cfg, logger):
        self.cfg = cfg
        self.logger = logger
        self.engine = None
        self.table = None

    # Function to Describe the table from config: Timestamp + Plant Name + one column per parameter.
    def _build_table(self):
        table_name = self.cfg.get("database", "table", default="kpi_readings")
        ts_col = self.cfg.get("database", "timestamp_column", default="Timestamp")
        plant_col = self.cfg.get("database", "plant_column", default="Plant Name")

        columns = [
            Column(ts_col, DateTime, primary_key=True, nullable=False),
            Column(plant_col, String(64), primary_key=True, nullable=False),
        ]

        # One DOUBLE column per configured parameter, named by its literal label
        for param in self.cfg.parameters():
            column_name = param.get("column") or param.get("name")
            columns.append(Column(column_name, Float, nullable=True))
        return Table(table_name, MetaData(), *columns)

    # Function to Add columns for any parameters that were added to config after the table was created.
    def _sync_columns(self):
        inspector = inspect(self.engine)
        existing = {c["name"] for c in inspector.get_columns(self.table.name)}
        preparer = self.engine.dialect.identifier_preparer

        missing = [c for c in self.table.columns if c.name not in existing]
        if not missing:
            return

        with self.engine.begin() as conn:
            for column in missing:
                column_type = column.type.compile(self.engine.dialect)
                ddl = (
                    f"ALTER TABLE {preparer.quote(self.table.name)} "
                    f"ADD COLUMN {preparer.quote(column.name)} {column_type} NULL"
                )
                conn.execute(text(ddl))
                self.logger.warning(f"Added missing column '{column.name}' to '{self.table.name}'")

    # Funtion to Create the engine, create the table if missing, add any new columns.
    def connect(self):
        url = self.cfg.get("database", "url")

        # Pool_pre_ping lets SQLAlchemy detect dropped connections and reconnect
        try:
            self.engine = create_engine(url, pool_pre_ping=True, future=True)
            self.table = self._build_table()
            self.table.metadata.create_all(self.engine)   # CREATE TABLE IF NOT EXISTS
            self._sync_columns()                          # ALTER TABLE for new parameters
            self.logger.info(f"Database connected (table '{self.table.name}')")
            return True

        except Exception as e:
            self.logger.error(f"Database connection failed: {e}")
            self.engine = None
            return False

    # Function to insert data into database
    def insert_row(self, row):
        if not row:
            return True

        if self.engine is None and not self.connect():
            return False

        try:
            with self.engine.begin() as conn:   # commits on success, rolls back on error
                conn.execute(insert(self.table), [row])
            return True

        except Exception as e:
            self.logger.error(f"Database insert failed: {e}")
            self.engine = None      # Force a reconnect on the next attempt
            return False

    def close(self):
        if self.engine is not None:
            try:
                self.engine.dispose()
            except Exception:
                pass
            self.engine = None
