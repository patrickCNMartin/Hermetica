# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import json
from collections.abc import Iterable

from seal.store import get_content
from utils.dates import as_iso, get_timestamp
from utils.error_handling import DuplicatedIdError
from utils.hashing import canonical_json, decode_entry, hash_bytes

# -----------------------------------------------------------------------------#
# LOCK DOCUMENT
# -----------------------------------------------------------------------------#


def manifest_hash(entries: dict[str, dict]) -> str:
    """Content hash of the pin set — the level-2 identity of a manifest."""
    return hash_bytes(canonical_json(entries))


def generate_protocol_lock(
    protocols: Iterable[str],
    db: str,
    as_of: int | None = None,
    provenance: dict | None = None,
    with_bodies: bool = True,
) -> dict:
    """Build the lock document for an already-resolved set of protocol hashes.

    `as_of` is the instant the pins represent and is only recorded, never used
    to resolve; `created_at` is when this ran. They diverge whenever the caller
    pins a past date. `with_bodies=False` leaves the blobs unread, which is all
    export_pins ever needs.
    """
    as_of = as_of if as_of is not None else get_timestamp()
    rows = get_content(db, protocols, with_blob=with_bodies)

    entries, display, bodies = {}, {}, {}
    for row in rows:
        if row.protocol_id in entries:
            raise DuplicatedIdError(
                f"two versions of protocol {row.protocol_id} in one lock; "
                "at most one version of a protocol may be active"
            )
        entries[row.protocol_id] = {"guid": row.protocol_guid, "hash": row.hash}
        display[row.protocol_id] = {
            "title": row.title,
            "doi": row.doi,
            "reserved_doi": row.reserved_doi,
            "uri": row.uri,
            "created_on": as_iso(row.created_on) if row.created_on else None,
            "creator": decode_entry(row.creator),
            "authors": decode_entry(row.authors),
        }
        if with_bodies:
            bodies[row.hash] = json.loads(row.protocol)

    document = {
        "manifest_hash": manifest_hash(entries),
        "as_of": as_iso(as_of),
        "created_at": as_iso(get_timestamp()),
        "provenance": provenance or {},
        "entries": entries,
        "protocols": display,
    }
    if with_bodies:
        document["bodies"] = bodies
    return document
