# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from collections.abc import Iterable
from dataclasses import dataclass

from seal.contract import ProtocolArtefact
from utils.dates import get_timestamp, to_epoch
from utils.hashing import encode_entry, canonical_json, hash_bytes
from utils.intervals import diff_versioned, write_version_control
from utils.store import fetch_entries, insert_statement
from utils.constants import (
    PROTOCOL_HISTORY,
    PROTOCOL_CONTENT,
    PROTOCOL_ID,
    PROTOCOL_CONTENT_FIELDS)

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


# -----------------------------------------------------------------------------#
# FORMATTING DB ENTRIES
# -----------------------------------------------------------------------------#
# Type enforce a protocol entry
@dataclass(frozen= True)
class ProtocolEntry:
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


def format_protocol_entry(
    artefacts: Iterable[ProtocolArtefact], pulled_at: int | None = None
) -> list[ProtocolEntry]:
    """make a list of entries from a bunch of protocols"""
    pulled_at = pulled_at if pulled_at is not None else get_timestamp()
    return [build_protocol_entry(artefact, pulled_at) for artefact in artefacts]


# -----------------------------------------------------------------------------#
# CHANGE DETECTION UTILS
# -----------------------------------------------------------------------------#
@dataclass(frozen=True)
class ProtocolContentEntry:
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
        db,
        protocol_content,
        columns,
        "hash",
        hashes,
        ProtocolContentEntry
    )


def diff_protocols(
    db: str,
    protocols: Iterable[ProtocolEntry],
    protocol_history: str = PROTOCOL_HISTORY,
    protocol_id: str = PROTOCOL_ID
) -> dict[str, list[str]]:
    """Compare a pull against the active state.

    Returns protocol_ids grouped as new / changed / unchanged / absent.
    """
    return diff_versioned(db, protocol_history, protocol_id, protocols)


# -----------------------------------------------------------------------------#
# WRITE CONTENT
# -----------------------------------------------------------------------------#

# personal pref - explicit argument naming
# Is it necessary? Not really. Do I find this more readable? Yes
def write_protocols(
    db: str,
    entries: list[ProtocolEntry],
    pulled_at: int | None = None,
    protocol_content: str = PROTOCOL_CONTENT,
    protocol_content_fields : Iterable[str] = PROTOCOL_CONTENT_FIELDS,
    protocol_history : str = PROTOCOL_HISTORY,
    protocol_id: str = PROTOCOL_ID
) -> dict[str, list[str]]:
    insert = insert_statement(protocol_content,protocol_content_fields)
    return write_version_control(
        db,
        protocol_history,
        protocol_id,
        insert,
        entries,
        pulled_at
    )
