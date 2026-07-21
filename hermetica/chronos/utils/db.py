# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import json
import sqlite3
from datetime import datetime, timezone


# -----------------------------------------------------------------------------#
# SCHEMA
# -----------------------------------------------------------------------------#
def initialize_db(db: str) -> None:
    """Create the protocol_versions table (and its index) if absent.

    Schema only: no network, no inserts. Safe to call on every run — it is a
    no-op once the table exists. The content hash is the primary key, which is
    what makes the store content-addressable: identical protocol content
    collapses to one row, and any change yields a new, verifiable version.
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
                valid_from    DATE NOT NULL,
                deprecated_at DATE
            )
            """
        )
        # A protocol_id has many versions (many hashes) over time; version
        # history is queried by protocol_id, so index it.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_protocol_id "
            "ON protocol_versions (protocol_id)"
        )


# -----------------------------------------------------------------------------#
# ROW MAPPING
# -----------------------------------------------------------------------------#
def _valid_from(protocol: dict) -> str:
    """Derive valid_from from the protocol's own created_on (unix epoch, UTC).

    protocols.io's list endpoint exposes created_on but NOT changed_on, so this
    records when the protocol lineage was created rather than when this specific
    version changed. Per-version timestamps would require the full-protocol
    detail endpoint — a future enhancement. Falls back to the current UTC date
    if created_on is absent so the NOT NULL constraint never breaks a pull.
    """
    created_on = protocol.get("created_on")
    if created_on:
        return (
            datetime.fromtimestamp(int(created_on), tz=timezone.utc)
            .date()
            .isoformat()
        )
    return datetime.now(timezone.utc).date().isoformat()


def to_rows(processed: dict) -> list[tuple]:
    """Map {content_hash: stripped_protocol} → rows matching the table columns.

    The `protocol` column stores the canonical JSON serialization (sorted keys,
    stable separators) — the exact form that was hashed, so the stored blob and
    its hash key stay consistent.
    """
    return [
        (
            content_hash,
            str(p["id"]),
            str(p["title"]),
            str(p["guid"]),
            json.dumps(p, sort_keys=True, separators=(",", ":")),
            _valid_from(p),
            None,  # deprecated_at: set when a newer version supersedes this one
        )
        for content_hash, p in processed.items()
    ]


# -----------------------------------------------------------------------------#
# INSERT
# -----------------------------------------------------------------------------#
def insert_protocols(db: str, rows: list[tuple]) -> int:
    """Batch-insert version rows in a single transaction.

    Idempotent: rows whose hash already exists are skipped (INSERT OR IGNORE),
    so re-pulling unchanged protocols costs nothing. Returns the number of NEW
    versions actually written (measured via total_changes, which is reliable
    across sqlite3 versions for executemany).
    """
    with sqlite3.connect(db) as conn:
        before = conn.total_changes
        conn.executemany(
            """
            INSERT OR IGNORE INTO protocol_versions
                (hash, protocol_id, protocol_name, protocol_guid, protocol, valid_from, deprecated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        return conn.total_changes - before
