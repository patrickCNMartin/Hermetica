# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import sqlite3

from seal.canonical import canonical_json, hash_bytes
from seal.contract import protocol_blob
from seal.dates import now_epoch, to_epoch


# -----------------------------------------------------------------------------#
# SCHEMA
# -----------------------------------------------------------------------------#
def initialize_db(db: str) -> None:
    """Create the protocol_versions table (and its index) if absent.

    All timestamps are unix epoch seconds (UTC). Human-readable dates are
    produced at the call boundary via seal.dates, never stored.
    """
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS protocol_versions (
                hash          TEXT PRIMARY KEY,
                protocol_id   TEXT NOT NULL,
                protocol_name TEXT NOT NULL,
                protocol_guid TEXT NOT NULL,
                protocol      TEXT NOT NULL,
                created_on    INTEGER,
                authors       TEXT,
                creator       TEXT,
                valid_from    INTEGER NOT NULL,
                deprecated_at INTEGER
            )
            """
        )
        # A protocol_id has many versions (many hashes) over time; version
        # history is queried by protocol_id, so index it.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_protocol_id "
            "ON protocol_versions (protocol_id)"
        )
        # "Active at instant T" scans the validity interval.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_validity "
            "ON protocol_versions (valid_from, deprecated_at)"
        )


# -----------------------------------------------------------------------------#
# ROW MAPPING
# -----------------------------------------------------------------------------#
def _valid_from(protocol: dict, pulled_at: int) -> int:
    """Backdate to the protocol's own created_on; fall back to the pull time.

    A protocol authored long before this store existed must resolve for dates
    before the first pull, so its interval opens at creation, not at ingest.
    """
    created_on = protocol.get("created_on")
    return to_epoch(created_on) if created_on else pulled_at


def _metadata_json(protocol: dict, field: str) -> str | None:
    """Structured metadata stored as canonical JSON; absent stays NULL."""
    value = protocol.get(field)
    return None if value is None else canonical_json(value).decode("ascii")


def to_row(protocol: dict, pulled_at: int | None = None) -> tuple:
    """Build one row, hashing the exact bytes that get stored."""
    pulled_at = pulled_at if pulled_at is not None else now_epoch()
    blob = protocol_blob(protocol)
    return (
        hash_bytes(blob),
        str(protocol["id"]),
        str(protocol["title"]),
        str(protocol["guid"]),
        blob.decode("ascii"),
        protocol.get("created_on"),
        _metadata_json(protocol, "authors"),
        _metadata_json(protocol, "creator"),
        _valid_from(protocol, pulled_at),
        None,
    )


def to_rows(processed: dict | list, pulled_at: int | None = None) -> list[tuple]:
    """Map selected protocols to table rows, sharing one pull timestamp."""
    protocols = processed.values() if isinstance(processed, dict) else processed
    pulled_at = pulled_at if pulled_at is not None else now_epoch()
    return [to_row(p, pulled_at) for p in protocols]


# -----------------------------------------------------------------------------#
# INSERT
# -----------------------------------------------------------------------------#
def insert_protocols(db: str, rows: list[tuple]) -> int:
    """Batch-insert version rows in a single transaction.

    Idempotent: rows whose hash already exists are skipped (INSERT OR IGNORE),
    so re-pulling unchanged protocols costs nothing. Returns the number of NEW
    versions actually written.
    """
    with sqlite3.connect(db) as conn:
        before = conn.total_changes
        conn.executemany(
            """
            INSERT OR IGNORE INTO protocol_versions
                (hash, protocol_id, protocol_name, protocol_guid, protocol,
                 created_on, authors, creator, valid_from, deprecated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        return conn.total_changes - before


# -----------------------------------------------------------------------------#
# CHANGE DETECTION
# -----------------------------------------------------------------------------#
def get_active_hashes(db: str) -> dict[str, str]:
    """protocol_id -> hash for every version not yet deprecated."""
    with sqlite3.connect(db) as conn:
        return dict(
            conn.execute(
                "SELECT protocol_id, hash FROM protocol_versions "
                "WHERE deprecated_at IS NULL"
            ).fetchall()
        )


def diff_pull(db: str, rows: list[tuple]) -> dict[str, list[str]]:
    """Compare a pull against the active state.

    Returns protocol_ids grouped as new / changed / unchanged / absent.
    """
    active = get_active_hashes(db)
    incoming = {row[1]: row[0] for row in rows}

    new, changed, unchanged = [], [], []
    for protocol_id, incoming_hash in incoming.items():
        if protocol_id not in active:
            new.append(protocol_id)
        elif active[protocol_id] != incoming_hash:
            changed.append(protocol_id)
        else:
            unchanged.append(protocol_id)

    return {
        "new": sorted(new),
        "changed": sorted(changed),
        "unchanged": sorted(unchanged),
        "absent": sorted(set(active) - set(incoming)),
    }


# -----------------------------------------------------------------------------#
# INTEGRITY
# -----------------------------------------------------------------------------#
def verify_protocols(db: str) -> list[str]:
    """Return hashes whose stored blob no longer hashes to its own key."""
    with sqlite3.connect(db) as conn:
        return [
            stored_hash
            for stored_hash, blob in conn.execute(
                "SELECT hash, protocol FROM protocol_versions"
            )
            if hash_bytes(blob.encode("ascii")) != stored_hash
        ]
