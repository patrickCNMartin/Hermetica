# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import json
from pathlib import Path
from typing import Any

from seal.dates import as_iso

# -----------------------------------------------------------------------------#
# CONSTANTS & STORES
# -----------------------------------------------------------------------------#
PULL_LOG_NAME = "pull_log.jsonl"

# -----------------------------------------------------------------------------#
# WRITE
# -----------------------------------------------------------------------------#
def log_path(log_dir: str) -> Path:
    return Path(log_dir) / PULL_LOG_NAME


def record_pull(log_dir: str, pulled_at: int, entry: dict[str, Any]) -> Path:
    """Append one pull to the log, creating it if this is the first."""
    path = log_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {"pulled_at": pulled_at, "pulled_at_iso": as_iso(pulled_at), **entry},
        sort_keys=True,
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return path


def read_pulls(db_dir: str) -> list[dict]:
    """Every logged pull, oldest first. Empty when nothing has run yet."""
    path = log_path(db_dir)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]
