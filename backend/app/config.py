import os                                            # Environment-variable overrides
import json                                          # Config is JSON now (was config.ini)
import sys                                           # Exit when config is missing
import threading                                     # Thread-safe access to config
from pathlib import Path                             # Path handling
from watchdog.observers import Observer              # Watch the config file for changes
from watchdog.events import FileSystemEventHandler   # Base class for file-system events

# Settings that an environment variable may override. This is what lets the
# SAME config.json work when run from a terminal and inside Docker, where
# "localhost" would otherwise point at the container instead of the host.

_ENV_OVERRIDES = {
    ("database", "url"): "KPI_DB_URL",
    ("database", "enabled"): "KPI_DB_ENABLED",
    ("database", "mysql", "url"): "KPI_DB_MYSQL_URL",
    ("database", "postgres", "url"): "KPI_DB_POSTGRES_URL",
    ("modbus", "host"): "KPI_MODBUS_HOST",
    ("modbus", "port"): "KPI_MODBUS_PORT",
    ("modbus", "slave_id"): "KPI_MODBUS_SLAVE_ID",
    ("modbus", "enabled"): "KPI_MODBUS_ENABLED",
    # Source selection, so a container can be pointed at a different input
    # without editing the bind-mounted config file.
    ("file_source", "enabled"): "KPI_FILE_SOURCE_ENABLED",
    ("file_source", "path"): "KPI_FILE_SOURCE_PATH",
    ("mysql_source", "enabled"): "KPI_MYSQL_SOURCE_ENABLED",
    ("mysql_source", "url"): "KPI_MYSQL_SOURCE_URL",
    ("postgres_source", "enabled"): "KPI_POSTGRES_SOURCE_ENABLED",
    ("postgres_source", "url"): "KPI_POSTGRES_SOURCE_URL",
    ("api", "host"): "KPI_API_HOST",
    ("api", "port"): "KPI_API_PORT",
    ("plant", "name"): "KPI_PLANT_NAME",
    ("timezone",): "KPI_TIMEZONE",
    ("reports", "timezone"): "KPI_REPORTS_TIMEZONE",
}

# Function to Turn an environment string into bool/int/float where that makes sense.
def _coerce(text):
    lowered = text.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text

# Class to Loads config.json and hot-reloads it whenever the file changes on disk.
class ConfigReloader(FileSystemEventHandler):
    def __init__(self, path, logger):
        self.path = Path(path).resolve()
        self.logger = logger

        if not self.path.exists():
            logger.error(f"Configuration file: {self.path} not found")
            sys.exit(1)

        self._lock = threading.Lock()
        self._data = {}
        self._load()
        self.last_modified = self.path.stat().st_mtime
    
    # Function to Read and parse the JSON file into memory.
    def _load(self):
        with open(self.path, "r", encoding="utf-8") as f:
            self._data = json.load(f)

    # Function that is Called by watchdog when a file in the watched directory changes
    def on_modified(self, event):
        if Path(event.src_path).name != self.path.name:
            return                                   # Ignore other files in the folder

        try:
            current_time = self.path.stat().st_mtime
        except OSError:
            return                                   # File briefly unavailable during save

        if current_time == self.last_modified:
            return                                   # Not an actual change

        with self._lock:
            previous = self._data
            try:
                self._load()
            except json.JSONDecodeError as e:
                # Invalid JSON: keep running on the previous config rather than dying
                self.logger.error(f"Config reload failed (invalid JSON): {e}. Keeping previous config.")
                self._data = previous
                return

            self.last_modified = current_time
            if previous != self._data:
                self.logger.warning("Config file reloaded due to modification.")

    # Function to Read a nested value, e.g. cfg.get('modbus', 'host').
    def get(self, *keys, default=None):
        env_name = _ENV_OVERRIDES.get(keys)
        if env_name:
            env_value = os.getenv(env_name)
            if env_value not in (None, ""):
                return _coerce(env_value)

        with self._lock:
            node = self._data
            for key in keys:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return node

    
    def parameters(self):
        """"""
        with self._lock:
            return list(self._data.get("parameters", []))

def start_config_watcher(reloader):
    """Watch the config file's directory so edits are picked up without a restart."""
    observer = Observer()
    observer.schedule(reloader, path=str(reloader.path.parent), recursive=False)
    observer.daemon = True      # Don't block process exit
    observer.start()
    return observer
