# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from datetime import date, datetime, timedelta, timezone

# -----------------------------------------------------------------------------#
# EPOCH <-> HUMAN
# -----------------------------------------------------------------------------#
# The store holds unix epoch seconds (UTC) only. Every human-readable form is
# produced at the call boundary, never persisted.
DAY = 86_400


def get_timestamp() -> int:
    """Current UTC time as epoch seconds."""
    return int(datetime.now(timezone.utc).timestamp())

# Using this approach since it is consistent with the way protocols.io does
# the whole time stamping thing. Their created_on label use epochs
def to_epoch(value: int | float | str | date | datetime) -> int:
    """Coerce a date, datetime or ISO-8601 string to epoch seconds (UTC).

    Naive inputs are read as UTC; a bare date lands on 00:00:00 of that day.
    """
    if isinstance(value, bool):
        raise TypeError("bool is not a date")
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp())
    if isinstance(value, date):
        return int(
            datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
            .timestamp()
        )
    raise TypeError(f"cannot convert {type(value).__name__} to epoch")


def from_epoch(epoch: int) -> datetime:
    """Epoch seconds -> timezone-aware UTC datetime."""
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc)


def as_date(epoch: int) -> str:
    """Epoch seconds -> 'YYYY-MM-DD' (UTC)."""
    return from_epoch(epoch).date().isoformat()


def as_iso(epoch: int) -> str:
    """Epoch seconds -> full ISO-8601 UTC string."""
    return from_epoch(epoch).isoformat()


def end_of_day(value: int | float | str | date | datetime) -> int:
    """Last instant of the UTC day containing `value`.

    The upper bound for "give me the state as of date D": take everything on or
    before D, not everything before D began.
    """
    start = datetime(*from_epoch(to_epoch(value)).timetuple()[:3], tzinfo=timezone.utc)
    return int((start + timedelta(days=1)).timestamp()) - 1
