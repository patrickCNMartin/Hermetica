# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from datetime import date, datetime, timezone

# -----------------------------------------------------------------------------#
# EPOCH <-> HUMAN
# -----------------------------------------------------------------------------#


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
            datetime(
                value.year, value.month, value.day, tzinfo=timezone.utc
            ).timestamp()
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
