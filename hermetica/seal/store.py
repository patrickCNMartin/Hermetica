# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime
from typing import NamedTuple

from seal.contract import METADATA_FIELDS, ProtocolArtefact
from utils.dates import get_timestamp, to_epoch
from utils.hashing import as_column, canonical_json, hash_bytes
from utils.intervals import (
    VersionInterval,
    close_intervals,
    diff_entries,
    open_intervals,
    seen_before,
    versions_on_date,
)
from utils.intervals import (
    active_hashes as _active_hashes,
)
from utils.store import (
    connect,
    fetch_rows,
    initialize_db,
    insert_statement,
    verify_blobs,
)

# -----------------------------------------------------------------------------#
# WHICH TABLES SEAL OWNS
# -----------------------------------------------------------------------------#
# Named here so nothing above seal — scribe, chronos, compose — has to learn a
# table name to ask seal a question.
CONTENT_TABLE = "protocol_content"
HISTORY_TABLE = "protocol_history"
ID_COLUMN = "protocol_id"


# -----------------------------------------------------------------------------#
# ERROR TYPE
# -----------------------------------------------------------------------------#
class DuplicateProtocolIdError(ValueError):
    """One pull carried two different versions of the same protocol_id."""


class UnknownProtocolHashError(ValueError):
    """A requested hash is not in protocol_content."""


# -----------------------------------------------------------------------------#
# SCHEMA
# -----------------------------------------------------------------------------#
SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS protocol_content (
        hash             TEXT PRIMARY KEY,
        protocol_id      TEXT NOT NULL,
        protocol_guid    TEXT NOT NULL,
        title            TEXT NOT NULL,
        doi              TEXT,
        reserved_doi     TEXT,
        uri              TEXT,
        protocol         TEXT NOT NULL,
        created_on       INTEGER,
        creator          TEXT,
        authors          TEXT,
        keywords         TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS protocol_history (
        protocol_id   TEXT NOT NULL,
        hash          TEXT NOT NULL REFERENCES protocol_content(hash),
        valid_from    INTEGER NOT NULL,
        deprecated_at INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS snapshots (
        manifest_hash TEXT PRIMARY KEY,
        created_at    INTEGER NOT NULL,
        provenance    TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_content_protocol_id "
    "ON protocol_content (protocol_id)",
    "CREATE INDEX IF NOT EXISTS idx_history_protocol_id "
    "ON protocol_history (protocol_id)",
    "CREATE INDEX IF NOT EXISTS idx_history_validity "
    "ON protocol_history (valid_from, deprecated_at)",
)


def initialize_protocol_db(db: str) -> None:
    """Create the content/history/snapshot tables and their indexes if absent."""
    initialize_db(db, SCHEMA)


# -----------------------------------------------------------------------------#
# FORMATTING DB ENTRIES
# -----------------------------------------------------------------------------#
# Type enforce a protocol entry
class ProtocolEntry(NamedTuple):
    hash: str
    protocol_id: str
    protocol_guid: str
    title: str
    doi: str | None
    reserved_doi: str | None
    uri: str | None
    protocol: str
    created_on: int | None
    creator: str | None
    authors: str | None
    keywords: str | None
    valid_from: int


# Derived, not restated: METADATA_FIELDS drives the metadata columns, so a field
# added there cannot silently misalign the insert.
_CONTENT_COLUMNS: tuple[str, ...] = (
    "hash",
    "protocol_id",
    "protocol_guid",
    "title",
    "doi",
    "reserved_doi",
    "uri",
    "protocol",
) + METADATA_FIELDS


def build_protocol_entry(
    artefact: ProtocolArtefact, pulled_at: int | None = None
) -> ProtocolEntry:
    """
    Just prepapring a new protocol entry from a ProtocolArtefact
    """
    pulled_at = pulled_at if pulled_at is not None else get_timestamp()
    blob = canonical_json(artefact.hashable())
    metadata = {k: as_column(v) for k, v in artefact.metadata().items()}
    created_on = metadata["created_on"]
    return ProtocolEntry(
        hash=hash_bytes(blob),
        protocol_id=str(artefact.id),
        protocol_guid=str(artefact.guid),
        title=artefact.title,
        doi=artefact.doi,
        reserved_doi=artefact.reserved_doi,
        uri=artefact.uri,
        protocol=blob.decode("ascii"),
        valid_from=to_epoch(created_on) if created_on else pulled_at,
        **metadata,
    )


def format_db_entry(
    artefacts: Iterable[ProtocolArtefact], pulled_at: int | None = None
) -> list[ProtocolEntry]:
    """make a list of entries from a bunch of protocols"""
    pulled_at = pulled_at if pulled_at is not None else get_timestamp()
    return [build_protocol_entry(artefact, pulled_at) for artefact in artefacts]


# -----------------------------------------------------------------------------#
# CHANGE DETECTION UTILS
# -----------------------------------------------------------------------------#
class ContentEntry(NamedTuple):
    hash: str
    protocol_id: str
    protocol_guid: str
    title: str
    doi: str | None
    reserved_doi: str | None
    uri: str | None
    created_on: int | None
    creator: str | None
    authors: str | None
    keywords: str | None
    protocol: str | None = None


def active_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    """protocol_id -> hash for every version currently active."""
    return _active_hashes(conn, HISTORY_TABLE, ID_COLUMN)


def _diff(
    conn: sqlite3.Connection, entries: Iterable[ProtocolEntry]
) -> dict[str, list[str]]:
    """This is more for logging purposes than anything else."""
    return diff_entries(
        active_hashes(conn), {row.protocol_id: row.hash for row in entries}
    )


# -----------------------------------------------------------------------------#
# GET CONTENT
# -----------------------------------------------------------------------------#

_READ_COLUMNS: tuple[str, ...] = ContentEntry._fields[:-1]


def get_content(
    db: str, hashes: Iterable[str], with_blob: bool = True
) -> list[ContentEntry]:
    # don't fetch actuall protocol if we only want the pins
    wanted = list(hashes)
    if not wanted:
        return []
    columns = _READ_COLUMNS + ("protocol",) if with_blob else _READ_COLUMNS
    with connect(db, read_only=True) as conn:
        found = {
            key: ContentEntry(*row)
            for key, row in fetch_rows(
                conn, CONTENT_TABLE, columns, "hash", wanted
            ).items()
        }
    missing = sorted(set(wanted) - set(found))
    if missing:
        raise UnknownProtocolHashError(f"not in protocol_content: {', '.join(missing)}")
    return [found[h] for h in wanted]


def protocols_on_date(
    conn: sqlite3.Connection, when: int | float | str | date | datetime
) -> dict[str, list[VersionInterval]]:
    """protocol_id -> every version that held the active slot on `when`'s UTC day."""
    return versions_on_date(conn, HISTORY_TABLE, ID_COLUMN, when)


def diff_pull(db: str, rows: Iterable[ProtocolEntry]) -> dict[str, list[str]]:
    """Compare a pull against the active state.

    Returns protocol_ids grouped as new / changed / unchanged / absent.
    """
    with connect(db, read_only=True) as conn:
        return _diff(conn, rows)


# -----------------------------------------------------------------------------#
# WRITE CONTENT
# -----------------------------------------------------------------------------#
_INSERT_CONTENT = insert_statement(CONTENT_TABLE, _CONTENT_COLUMNS)


def write_pull(
    db: str, entries: list[ProtocolEntry], pulled_at: int | None = None
) -> dict[str, list[str]]:
    """Apply one pull and return its diff."""
    pulled_at = pulled_at if pulled_at is not None else get_timestamp()

    seen = {row.protocol_id for row in entries}
    if len(seen) != len(entries):
        raise DuplicateProtocolIdError(
            "a pull carried two versions of the same protocol_id; "
            "at most one version of a protocol may be active"
        )

    with connect(db) as conn:
        diff = _diff(conn, entries)
        first_time = set(diff["new"]) - seen_before(
            conn, HISTORY_TABLE, ID_COLUMN, diff["new"]
        )
        opening = set(diff["new"]) | set(diff["changed"])
        closing = set(diff["changed"]) | set(diff["absent"])
        fresh = [row for row in entries if row.protocol_id in opening]

        # Bound by name, so valid_from riding along unreferenced is harmless.
        conn.executemany(_INSERT_CONTENT, [row._asdict() for row in fresh])
        close_intervals(conn, HISTORY_TABLE, ID_COLUMN, closing, pulled_at)
        open_intervals(
            conn,
            HISTORY_TABLE,
            ID_COLUMN,
            [
                (
                    row.protocol_id,
                    row.hash,
                    row.valid_from if row.protocol_id in first_time else pulled_at,
                )
                for row in fresh
            ],
        )
    return diff


# -----------------------------------------------------------------------------#
# VERIFY CONTENT
# -----------------------------------------------------------------------------#
def verify_protocols(db: str) -> list[str]:
    """Return hashes whose stored blob no longer hashes to its own key."""
    return verify_blobs(db, CONTENT_TABLE, "hash", "protocol")
