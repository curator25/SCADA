import pytz          # IANA timezone database

# --------------------------------------------------------------------------- #
# Timezone settings, resolved in one place.
#
# There are three, and they are deliberately independent:
#
#   timezone              what the stored timestamps mean (writer + scheduling)
#   reports.timezone      what the CSV exports are rendered in
#   kpi.month_timezone    which calendar month picks the tmod_monthly value
#
# Any IANA name works ("UTC", "Asia/Kolkata", "America/New_York", ...).
# An unknown name falls back instead of killing a thread, but it is always
# reported - a silent fallback would quietly shift every timestamp by hours.
# --------------------------------------------------------------------------- #

_warned = set()     # Names already reported, so the log is not spammed


def is_valid_timezone(name):
    """True if `name` is a timezone the system understands."""
    if not name:
        return False
    try:
        pytz.timezone(name)
        return True
    except Exception:
        return False


def resolve_timezone(name, logger=None, fallback=pytz.UTC):
    """Turn a configured timezone name into a tzinfo, reporting bad names once."""
    if not name:
        return fallback

    try:
        return pytz.timezone(name)
    except Exception:
        if logger is not None and name not in _warned:
            _warned.add(name)
            logger.error(
                f"Unknown timezone '{name}' - falling back to {fallback}. "
                f"Use an IANA name, e.g. 'UTC', 'Asia/Kolkata', 'Europe/Madrid'."
            )
        return fallback


def validate_timezones(cfg, logger):
    """Check every configured timezone at startup. Returns the bad ones."""
    settings = [
        ("timezone", cfg.get("timezone", default="UTC")),
        ("reports.timezone", cfg.get("reports", "timezone", default=None)),
        ("kpi.month_timezone", cfg.get("kpi", "month_timezone", default=None)),
    ]

    problems = []
    for key, value in settings:
        if value is None:
            continue                # Not set - the caller falls back on purpose
        if not is_valid_timezone(value):
            problems.append(key)
            logger.error(
                f"Config '{key}' is '{value}', which is not a known timezone. "
                f"Timestamps will fall back to UTC. Use an IANA name such as "
                f"'Asia/Kolkata'."
            )

    return problems
