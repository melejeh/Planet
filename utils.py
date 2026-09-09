import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import session

DEFAULT_TIMEZONE = os.environ.get("PLANET_TIMEZONE", "America/Toronto")

try:
    ZoneInfo(DEFAULT_TIMEZONE)
except (ZoneInfoNotFoundError, ValueError):
    DEFAULT_TIMEZONE = "America/Toronto"


def _valid_timezone_name(value):
    timezone_name = str(value or "").strip()
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return timezone_name


def _ensure_user_timezone_column(connection):
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(users)").fetchall()
    }
    if "timezone" not in columns:
        connection.execute(
            "ALTER TABLE users ADD COLUMN timezone TEXT NOT NULL "
            "DEFAULT 'America/Toronto'"
        )
        connection.commit()


def planet_now():
    """Return local time for the currently signed-in Planet user."""
    timezone_name = _valid_timezone_name(
        session.get("timezone", DEFAULT_TIMEZONE)
    ) or DEFAULT_TIMEZONE
    return datetime.now(ZoneInfo(timezone_name)).replace(tzinfo=None)


def pretty_date(value):
    """Jinja template filter: '2026-09-09' -> 'September 9, 2026'."""
    if not value:
        return ""

    formatted_date = datetime.strptime(value, "%Y-%m-%d").strftime("%B %d, %Y")
    return formatted_date.replace(" 0", " ")
