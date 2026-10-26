from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def shanghai_now(value: datetime | None = None) -> datetime:
    """Use one explicit business timezone, independent of the host timezone."""
    if value is None:
        return datetime.now(SHANGHAI)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(SHANGHAI)
