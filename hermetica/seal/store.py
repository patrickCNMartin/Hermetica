# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from collections.abc import Iterable
from typing import NamedTuple

from seal.contract import ProtocolArtefact
from utils.constants import (
    PROTOCOL_CONTENT,
    PROTOCOL_CONTENT_FIELDS,
    PROTOCOL_GUID,
    PROTOCOL_HISTORY,
    PROTOCOL_SOURCE,
    PROTOCOL_UID,
)
from utils.dates import get_timestamp, to_epoch
from utils.hashing import canonical_json, encode_entry, hash_bytes
from utils.intervals import (
    active_entries,
    intervals_of,
    latest_entries,
    version_control_diff,
    write_version_control,
)
from utils.store import (
    append_only_triggers,
    fetch_entries,
    immutable_triggers,
    insert_statement,
)

# -----------------------------------------------------------------------------#
# SCHEMA
# -----------------------------------------------------------------------------#
SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS protocol_content (
        hash             TEXT PRIMARY KEY,
        protocol_uid     TEXT NOT NULL,
        source           TEXT NOT NULL,
        protocol_id      TEXT NOT NULL,
        protocol_guid    TEXT NOT NULL,
        title            TEXT NOT NULL,
        doi              TEXT,
        reserved_doi     TEXT,
        uri              TEXT,
        executor         TEXT,
        protocol         TEXT NOT NULL,
        created_on       INTEGER,
        creator          TEXT,
        authors          TEXT,
        keywords         TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS protocol_history (
        protocol_uid  TEXT NOT NULL,
        source        TEXT NOT NULL,
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
    "CREATE INDEX IF NOT EXISTS idx_content_protocol_uid "
    "ON protocol_content (protocol_uid)",
    "CREATE INDEX IF NOT EXISTS idx_history_protocol_uid "
    "ON protocol_history (protocol_uid)",
    "CREATE INDEX IF NOT EXISTS idx_history_source "
    "ON protocol_history (source, deprecated_at)",
    "CREATE INDEX IF NOT EXISTS idx_history_validity "
    "ON protocol_history (valid_from, deprecated_at)",
    # One active version per protocol, as a database rule rather than a Python
    # hope — this is what makes the write path safe without re-checking.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_history_one_active "
    "ON protocol_history (protocol_uid) WHERE deprecated_at IS NULL",
    *immutable_triggers(PROTOCOL_CONTENT),
    *append_only_triggers(
        PROTOCOL_HISTORY, ("protocol_uid", "source", "hash", "valid_from")
    ),
)


# -----------------------------------------------------------------------------#
# FORMATTING DB ENTRIES
# -----------------------------------------------------------------------------#
# Type enforce a protocol entry


class ProtocolEntry(NamedTuple):
    hash: str
    protocol_uid: str
    source: str
    protocol_id: str
    protocol_guid: str
    title: str
    doi: str | None
    reserved_doi: str | None
    uri: str | None
    executor: str | None
    protocol: str
    created_on: int | None
    creator: str | None
    authors: str | None
    keywords: str | None
    valid_from: int


def build_protocol_entry(
    artefact: ProtocolArtefact, pulled_at: int | None = None
) -> ProtocolEntry:
    """
    Just prepapring a new protocol entry from a ProtocolArtefact
    """
    pulled_at = pulled_at if pulled_at is not None else get_timestamp()
    blob = canonical_json(artefact.hashable())
    metadata = {k: encode_entry(v) for k, v in artefact.metadata().items()}
    created_on = metadata["created_on"]
    return ProtocolEntry(
        hash=hash_bytes(blob),
        protocol_uid=f"{artefact.source}:{artefact.id}",
        source=artefact.source,
        protocol_id=str(artefact.id),
        protocol_guid=str(artefact.guid),
        title=artefact.title,
        doi=artefact.doi,
        reserved_doi=artefact.reserved_doi,
        uri=artefact.uri,
        executor=artefact.executor,
        protocol=blob.decode("ascii"),
        valid_from=to_epoch(created_on) if created_on else pulled_at,
        **metadata,
    )


def format_protocol_entry(
    artefacts: Iterable[ProtocolArtefact], pulled_at: int | None = None
) -> list[ProtocolEntry]:
    """make a list of entries from a bunch of protocols"""
    pulled_at = pulled_at if pulled_at is not None else get_timestamp()
    return [build_protocol_entry(artefact, pulled_at) for artefact in artefacts]


# -----------------------------------------------------------------------------#
# CHANGE DETECTION UTILS
# -----------------------------------------------------------------------------#


class ProtocolContentEntry(NamedTuple):
    hash: str
    protocol_uid: str
    source: str
    protocol_id: str
    protocol_guid: str
    title: str
    doi: str | None
    reserved_doi: str | None
    uri: str | None
    executor: str | None
    created_on: int | None
    creator: str | None
    authors: str | None
    keywords: str | None
    protocol: str | None = None


# -----------------------------------------------------------------------------#
# GET CONTENT
# -----------------------------------------------------------------------------#
def read_protocol_content():
    return ProtocolContentEntry._fields[:-1]


def get_protocols(
    db: str,
    hashes: Iterable[str],
    with_blob: bool = True,
    protocol_content: str = PROTOCOL_CONTENT,
) -> list[ProtocolContentEntry]:
    READ_COLUMNS = read_protocol_content()
    columns = READ_COLUMNS + ("protocol",) if with_blob else READ_COLUMNS
    return fetch_entries(
        db, protocol_content, columns, "hash", hashes, ProtocolContentEntry
    )


def active_protocols(db: str) -> list[dict]:
    """Every protocol's active version, without its body."""
    return active_entries(
        db, PROTOCOL_HISTORY, PROTOCOL_CONTENT, PROTOCOL_UID, read_protocol_content()
    )


def latest_protocols(
    db: str, keys: Iterable[str], by: str = PROTOCOL_GUID
) -> dict[str, dict]:
    """Each protocol's latest version, keyed by `protocol_guid` or `protocol_uid`.

    Inactive ones are included — `deprecated_at` says which. A key no pull ever
    sealed is absent.
    """
    return latest_entries(
        db, PROTOCOL_HISTORY, PROTOCOL_CONTENT, by, keys, read_protocol_content()
    )


def protocol_intervals(db: str, protocol_uid: str) -> list[dict]:
    """Every version one protocol has held, oldest first."""
    return intervals_of(db, PROTOCOL_HISTORY, PROTOCOL_UID, protocol_uid)


def scope_of(
    entries: Iterable[ProtocolEntry], source: str | None = None
) -> tuple[str, str]:
    """The (column, value) partition one pull writes into.

    Read off the entries, which carry it, so a declared source cannot disagree
    with what is being written. An empty pull has no entries to read and **must**
    be told: unscoped, it would deprecate every other platform by absence.
    """
    found = sorted({entry.source for entry in entries})
    if source is None:
        if len(found) != 1:
            raise ValueError(
                f"cannot infer the partition from {len(found)} sources {found}; "
                "pass source — an empty pull deprecates its own source only"
            )
        return (PROTOCOL_SOURCE, found[0])
    if disagree := [name for name in found if name != source]:
        raise ValueError(
            f"entries from {', '.join(disagree)} in a {source} pull; "
            "a pull writes one platform's partition"
        )
    return (PROTOCOL_SOURCE, source)


def diff_protocols(
    db: str,
    protocols: Iterable[ProtocolEntry],
    source: str | None = None,
    protocol_history: str = PROTOCOL_HISTORY,
    protocol_uid: str = PROTOCOL_UID,
) -> dict[str, list[str]]:
    """Compare a pull against the active state, within one source's partition.

    Returns protocol_uids grouped as new / changed / unchanged / absent.
    """
    protocols = list(protocols)
    return version_control_diff(
        db, protocol_history, protocol_uid, protocols, scope_of(protocols, source)
    )


# -----------------------------------------------------------------------------#
# WRITE CONTENT
# -----------------------------------------------------------------------------#


# personal pref - explicit argument naming
# Is it necessary? Not really. Do I find this more readable? Yes
def write_protocols(
    db: str,
    entries: list[ProtocolEntry],
    pulled_at: int | None = None,
    source: str | None = None,
    protocol_content: str = PROTOCOL_CONTENT,
    protocol_content_fields: Iterable[str] = PROTOCOL_CONTENT_FIELDS,
    protocol_history: str = PROTOCOL_HISTORY,
    protocol_uid: str = PROTOCOL_UID,
) -> dict[str, list[str]]:
    """Apply one source's pull. Absence is computed inside that source alone."""
    insert = insert_statement(protocol_content, protocol_content_fields)
    return write_version_control(
        db,
        protocol_history,
        protocol_uid,
        insert,
        entries,
        pulled_at,
        scope_of(entries, source),
    )
