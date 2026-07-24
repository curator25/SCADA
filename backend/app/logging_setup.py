import logging                                       # Standard logging
from logging.handlers import RotatingFileHandler     # Rotating log files
from pathlib import Path                             # Log directory handling
import colorlog                                      # Coloured console output


def setup_logging(name="kpi", level=logging.INFO):
    """Create a logger that writes to a rotating file and a coloured console."""
    # Drop any handlers attached to the root logger so subprocesses don't duplicate lines
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    log_dir = Path(__file__).resolve().parent.parent / "logs"   # <backend>/logs
    log_dir.mkdir(exist_ok=True)                                # Create it if missing

    # ---- file handler: rotates at 10 MB, keeps 5 old files ----
    file_handler = RotatingFileHandler(
        log_dir / f"{name}.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(
        logging.Formatter("[%(asctime)s] - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )

    # ---- console handler: same message, colour-coded by level ----
    console_handler = colorlog.StreamHandler()
    console_handler.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s[%(asctime)s] - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors={
                "DEBUG": "blue",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red",
            },
        )
    )

    logger = logging.getLogger(name)
    logger.handlers.clear()             # Avoid duplicate handlers if called twice
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.setLevel(level)
    logger.propagate = False            # Don't bubble up to the root logger

    logging.getLogger("pymodbus").disabled = True   # Silence pymodbus internal chatter
    return logger
