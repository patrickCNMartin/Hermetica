# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from utils.dates import as_iso


# -----------------------------------------------------------------------------#
# WHAT BOTH PORTS SHARE
# -----------------------------------------------------------------------------#
class InvalidRequestError(ValueError):
    """A request the ports refuse before touching a store. Every problem is
    listed, so a caller fixes them in one round trip rather than one each."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("invalid request: " + "; ".join(problems))


# -----------------------------------------------------------------------------#
# SHAPING
# -----------------------------------------------------------------------------#
def describe_intervals(intervals: list[dict]) -> list[dict]:
    """Every version an id has held, as the API shows it: ISO times, and no end
    for the one still active."""
    return [
        {
            "hash": interval["hash"],
            "valid_from": as_iso(interval["valid_from"]),
            "deprecated_at": (
                as_iso(interval["deprecated_at"])
                if interval["deprecated_at"] is not None
                else None
            ),
        }
        for interval in intervals
    ]
