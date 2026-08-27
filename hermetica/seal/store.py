# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, NamedTuple

from seal.contract import (
    METADATA_FIELDS,
    ProtocolArtefact,
    canonical_json,
    hash_bytes,
    protocol_blob,
)
from seal.dates import end_of_day, get_timestamp, start_of_day, to_epoch


# -----------------------------------------------------------------------------#
# ERROR TYPE
# -----------------------------------------------------------------------------#
class DuplicateProtocolIdError(ValueError):
    """One pull carried two different versions of the same protocol_id."""


class UnknownProtocolHashError(ValueError):
    """A requested hash is not in protocol_content."""


# -----------------------------------------------------------------------------#
# CONNECTION
# -----------------------------------------------------------------------------#
@contextmanager
def connect(db: str, read_only: bool = False) -> Iterator[sqlite3.Connection]:
    """
    Open a connection, commit or roll back, and always close it.
    """
    uri = f"file:{db}?mode=ro" if read_only else db
    conn = sqlite3.connect(uri, uri=read_only)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            yield conn
    finally:
        conn.close()


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
    with connect(db) as conn:
        for statement in SCHEMA:
            conn.execute(statement)


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


def _as_column(value: Any) -> Any:
    """
    Just making sure that if it is not a straight forward value we convert it to
    a canonical_json (essentially always in the same order and as ascii)
    """
    if value is None or isinstance(value, (int, float, str)):
        return value
    return canonical_json(value).decode("ascii")


def build_protocol_entry(
    artefact: ProtocolArtefact, pulled_at: int | None = None
) -> ProtocolEntry:
    """
    Just prepapring a new protocol entry from a ProtocolArtefact
    """
    pulled_at = pulled_at if pulled_at is not None else get_timestamp()
    blob = protocol_blob(artefact)
    metadata = {k: _as_column(v) for k, v in artefact.metadata().items()}
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
# Type set enforcing
class VersionInterval(NamedTuple):
    hash: str
    valid_from: int
    deprecated_at: int | None


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
    """which protocols are actully active using row factories
    Some kind of way to access rows repeatidly in your db?
    """
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    return {
        row["protocol_id"]: row["hash"]
        for row in cursor.execute(
            "SELECT protocol_id, hash FROM protocol_history WHERE deprecated_at IS NULL"
        )
    }


def _diff(
    conn: sqlite3.Connection, entries: Iterable[ProtocolEntry]
) -> dict[str, list[str]]:
    """This is more for logging purposes than anything else."""
    active = active_hashes(conn)
    incoming = {row.protocol_id: row.hash for row in entries}

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


def _seen_before(conn: sqlite3.Connection, ids: list[str]) -> set[str]:
    """Which of these protocol_ids already have history, active or closed."""
    if not ids:
        return set()
    slots = ",".join("?" * len(ids))
    return {
        pid
        for (pid,) in conn.execute(
            f"SELECT DISTINCT protocol_id FROM protocol_history "
            f"WHERE protocol_id IN ({slots})",
            ids,
        )
    }


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
    slots = ",".join("?" * len(wanted))
    with connect(db, read_only=True) as conn:
        found = {
            row[0]: ContentEntry(*row)
            for row in conn.execute(
                f"SELECT {', '.join(columns)} "
                f"FROM protocol_content WHERE hash IN ({slots})",
                wanted,
            )
        }
    missing = sorted(set(wanted) - set(found))
    if missing:
        raise UnknownProtocolHashError(f"not in protocol_content: {', '.join(missing)}")
    return [found[h] for h in wanted]


def protocols_on_date(
    conn: sqlite3.Connection, when: int | float | str | date | datetime
) -> dict[str, list[VersionInterval]]:
    """protocol_id -> every version that held the active slot on `when`'s UTC day."""
    opens, closes = start_of_day(when), end_of_day(when)
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    versions: dict[str, list[VersionInterval]] = {}
    for row in cursor.execute(
        "SELECT protocol_id, hash, valid_from, deprecated_at FROM protocol_history "
        "WHERE valid_from <= ? "
        "AND (deprecated_at IS NULL OR deprecated_at > ?) "
        "ORDER BY protocol_id, valid_from",
        (closes, opens),
    ):
        versions.setdefault(row["protocol_id"], []).append(
            VersionInterval(row["hash"], row["valid_from"], row["deprecated_at"])
        )
    return versions


def diff_pull(db: str, rows: Iterable[ProtocolEntry]) -> dict[str, list[str]]:
    """Compare a pull against the active state.

    Returns protocol_ids grouped as new / changed / unchanged / absent.
    """
    with connect(db, read_only=True) as conn:
        return _diff(conn, rows)


# -----------------------------------------------------------------------------#
# WRITE CONTENT
# -----------------------------------------------------------------------------#
_INSERT_CONTENT = (
    f"INSERT OR IGNORE INTO protocol_content ({', '.join(_CONTENT_COLUMNS)}) "
    f"VALUES ({', '.join(':' + column for column in _CONTENT_COLUMNS)})"
)

_CLOSE_INTERVAL = """
    UPDATE protocol_history SET deprecated_at = ?
    WHERE protocol_id = ? AND deprecated_at IS NULL
"""

_OPEN_INTERVAL = """
    INSERT INTO protocol_history (protocol_id, hash, valid_from, deprecated_at)
    VALUES (?, ?, ?, NULL)
"""


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
        first_time = set(diff["new"]) - _seen_before(conn, diff["new"])
        opening = set(diff["new"]) | set(diff["changed"])
        closing = set(diff["changed"]) | set(diff["absent"])
        fresh = [row for row in entries if row.protocol_id in opening]

        # Bound by name, so valid_from riding along unreferenced is harmless.
        conn.executemany(_INSERT_CONTENT, [row._asdict() for row in fresh])
        conn.executemany(_CLOSE_INTERVAL, [(pulled_at, pid) for pid in sorted(closing)])
        conn.executemany(
            _OPEN_INTERVAL,
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
    with connect(db, read_only=True) as conn:
        return [
            stored_hash
            for stored_hash, blob in conn.execute(
                "SELECT hash, protocol FROM protocol_content"
            )
            if hash_bytes(blob.encode("ascii")) != stored_hash
        ]
