# -----------------------------------------------------------------------------#
# THE QUERY PORT — chronos.db, read-only
# -----------------------------------------------------------------------------#
"""What outside tools may ask of the protocol store. Every read goes through a
read-only connection; nothing here writes, and a test holds that against a file
with no write permission."""

import json

from api.contract import describe_intervals
from seal.store import active_protocols, get_protocols, protocol_intervals
from utils.dates import as_iso
from utils.hashing import decode_entry


# -----------------------------------------------------------------------------#
# ERRORS
# -----------------------------------------------------------------------------#
class UnknownProtocolError(ValueError):
    """A protocol_uid with no history at all."""

    def __init__(self, protocol_uid: str):
        self.protocol_uid = protocol_uid
        super().__init__(f"no protocol {protocol_uid!r} has ever been sealed")


# -----------------------------------------------------------------------------#
# SHAPING
# -----------------------------------------------------------------------------#
def describe(entry: dict) -> dict:
    """A stored entry as the API shows it: stable ids, ISO times, decoded JSON."""
    return {
        "protocol_uid": entry["protocol_uid"],
        "protocol_guid": entry["protocol_guid"],
        "source": entry["source"],
        "protocol_id": entry["protocol_id"],
        "hash": entry["hash"],
        "title": entry["title"],
        "executor": entry["executor"],
        "doi": entry["doi"],
        "reserved_doi": entry["reserved_doi"],
        "uri": entry["uri"],
        "created_on": as_iso(entry["created_on"]) if entry["created_on"] else None,
        "creator": decode_entry(entry["creator"]),
        "authors": decode_entry(entry["authors"]),
    }


# -----------------------------------------------------------------------------#
# THE PORT
# -----------------------------------------------------------------------------#
def list_protocols(db: str) -> list[dict]:
    """Every protocol's active version, without its body."""
    return [
        {**describe(entry), "valid_from": as_iso(entry["valid_from"])}
        for entry in active_protocols(db)
    ]


def protocol_versions(db: str, protocol_uid: str) -> dict:
    """Every version one protocol has held, oldest first."""
    intervals = protocol_intervals(db, protocol_uid)
    if not intervals:
        raise UnknownProtocolError(protocol_uid)
    return {"protocol_uid": protocol_uid, "versions": describe_intervals(intervals)}


def get_protocol(db: str, digest: str) -> dict:
    """One version by hash, body included. Raises MissingHash if unknown."""
    (entry,) = get_protocols(db, [digest])
    return {**describe(entry._asdict()), "protocol": json.loads(entry.protocol)}
