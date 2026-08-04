# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any, NamedTuple

from seal.contract import (
    METADATA_FIELDS,
    ProtocolArtefact,
    canonical_json,
    hash_bytes,
    protocol_blob,
)
from seal.dates import get_timestamp, to_epoch


class DuplicateProtocolIdError(ValueError):
    """One pull carried two different versions of the same protocol_id."""


class UnknownProtocolHashError(ValueError):
    """A requested hash is not in protocol_content."""


# -----------------------------------------------------------------------------#
# CONNECTION
# -----------------------------------------------------------------------------#
@contextmanager
def connect(db: str, read_only: bool = False) -> Iterator[sqlite3.Connection]:
    """Open a connection, commit or roll back, and always close it.

    `with sqlite3.connect(...)` commits but never closes; the close has to be
    in a finally of our own.
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
# Content is addressed by hash and never deleted; history carries the validity
# intervals. Metadata rides on the content row: it is not hashed, but it does
# not change without the content changing either.
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
        last_modified_on INTEGER
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


def initialize_db(db: str) -> None:
    """Create the content/history/snapshot tables and their indexes if absent."""
    with connect(db) as conn:
        for statement in SCHEMA:
            conn.execute(statement)


# -----------------------------------------------------------------------------#
# FORMATTING DB ENTRIES
# -----------------------------------------------------------------------------#
class ProtocolRow(NamedTuple):
    """One pulled protocol, spanning protocol_content and protocol_history.

    Field names match protocol_content's columns — the insert binds by name.
    """

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
    last_modified_on: int | None
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
    """Scalars bind directly; structured metadata is stored as canonical JSON."""
    if value is None or isinstance(value, (int, float, str)):
        return value
    return canonical_json(value).decode("ascii")


def build_row(artefact: ProtocolArtefact, pulled_at: int | None = None) -> ProtocolRow:
    """Build one row, hashing the exact bytes that get stored.

    valid_from backdates to the protocol's own created_on so a protocol authored
    before this store existed still resolves for earlier dates. write_pull
    overrides this for a protocol_id that already has history — see there.
    """
    pulled_at = pulled_at if pulled_at is not None else get_timestamp()
    blob = protocol_blob(artefact)
    metadata = {k: _as_column(v) for k, v in artefact.metadata().items()}
    created_on = metadata["created_on"]
    return ProtocolRow(
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


def format_entry(
    artefacts: Iterable[ProtocolArtefact], pulled_at: int | None = None
) -> list[ProtocolRow]:
    """Map protocol artefacts to rows, sharing one pull timestamp."""
    pulled_at = pulled_at if pulled_at is not None else get_timestamp()
    return [build_row(artefact, pulled_at) for artefact in artefacts]


# -----------------------------------------------------------------------------#
# CHANGE DETECTION
# -----------------------------------------------------------------------------#
def active_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    """protocol_id -> hash for every version not yet deprecated.

    Takes a connection, not a path: write_pull needs this inside its open
    transaction, and a caller holding only a path can wrap it in `connect`.
    """
    # Scoped to the cursor, not the connection: this borrows write_pull's open
    # transaction and must not change how its other reads come back.
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    return {
        row["protocol_id"]: row["hash"]
        for row in cursor.execute(
            "SELECT protocol_id, hash FROM protocol_history WHERE deprecated_at IS NULL"
        )
    }




def _diff(
    conn: sqlite3.Connection, rows: Iterable[ProtocolRow]
) -> dict[str, list[str]]:
    active = active_hashes(conn)
    incoming = {row.protocol_id: row.hash for row in rows}

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


def diff_pull(db: str, rows: Iterable[ProtocolRow]) -> dict[str, list[str]]:
    """Compare a pull against the active state.

    Returns protocol_ids grouped as new / changed / unchanged / absent.
    """
    with connect(db, read_only=True) as conn:
        return _diff(conn, rows)


# -----------------------------------------------------------------------------#
# CONTENT READS
# -----------------------------------------------------------------------------#
class ContentRow(NamedTuple):
    """One stored protocol. `protocol` is the blob, omitted unless asked for."""

    hash: str
    protocol_id: str
    protocol_guid: str
    title: str
    doi: str | None
    reserved_doi: str | None
    uri: str | None
    created_on: int | None
    authors: str | None
    last_modified_on: int | None
    protocol: str | None = None


_READ_COLUMNS: tuple[str, ...] = ContentRow._fields[:-1]


def get_content(
    db: str, hashes: Iterable[str], with_blob: bool = True
) -> list[ContentRow]:
    """Fetch stored protocols by hash, in the order asked for.

    The blob dwarfs every other column, so `with_blob=False` skips it for
    callers that only need the pinned identity. Every requested hash must
    resolve: a bad pin shrinking a lock silently is the failure this guards
    against.
    """
    wanted = list(hashes)
    if not wanted:
        return []
    columns = _READ_COLUMNS + ("protocol",) if with_blob else _READ_COLUMNS
    slots = ",".join("?" * len(wanted))
    with connect(db, read_only=True) as conn:
        found = {
            row[0]: ContentRow(*row)
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


# -----------------------------------------------------------------------------#
# WRITE PATH
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
    db: str, rows: list[ProtocolRow], pulled_at: int | None = None
) -> dict[str, list[str]]:
    """Apply one pull and return its diff.

    deprecate-on-change closes the prior interval and opens a new one;
    deprecate-on-absence closes protocols that vanished from the pull.
    Only a protocol_id's first-ever version may backdate to created_on — any
    later one doing so would reopen inside a closed interval and leave two
    versions resolving as active at the same instant. "First-ever" means no
    history at all, not merely no live version: a protocol that disappears and
    comes back must open at the pull that found it again.
    """
    pulled_at = pulled_at if pulled_at is not None else get_timestamp()

    seen = {row.protocol_id for row in rows}
    if len(seen) != len(rows):
        raise DuplicateProtocolIdError(
            "a pull carried two versions of the same protocol_id; "
            "at most one version of a protocol may be active"
        )

    with connect(db) as conn:
        diff = _diff(conn, rows)
        first_time = set(diff["new"]) - _seen_before(conn, diff["new"])
        opening = set(diff["new"]) | set(diff["changed"])
        closing = set(diff["changed"]) | set(diff["absent"])
        fresh = [row for row in rows if row.protocol_id in opening]

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
# INTEGRITY
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
