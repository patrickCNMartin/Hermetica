# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import json
from collections.abc import Iterable

from seal.store import get_protocols
from compose.store import get_pipelines
from utils.dates import as_iso, get_timestamp
from utils.hashing import canonical_json, decode_entry, hash_bytes

# -----------------------------------------------------------------------------#
# Error handling
# -----------------------------------------------------------------------------#
class DuplicatedIdError(ValueError):
    """Duplicated entries in the version control data base"""

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
    """
    as_of = as_of if as_of is not None else get_timestamp()
    protocols = get_protocols(db, protocols, with_blob=with_bodies)

    entries, display, bodies = {}, {}, {}
    for protocol in protocols:
        if protocol.protocol_id in entries:
            raise DuplicatedIdError(
                f"two versions of protocol {protocol.protocol_id} in one lock; "
                "at most one version of a protocol may be active"
            )
        entries[protocol.protocol_id] = {"guid": protocol.protocol_guid, "hash": protocol.hash}
        display[protocol.protocol_id] = {
            "title": protocol.title,
            "doi": protocol.doi,
            "reserved_doi": protocol.reserved_doi,
            "uri": protocol.uri,
            "created_on": as_iso(protocol.created_on) if protocol.created_on else None,
            "creator": decode_entry(protocol.creator),
            "authors": decode_entry(protocol.authors),
        }
        if with_bodies:
            bodies[protocol.hash] = json.loads(protocol.protocol)

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

def generate_pipeline_lock(
    pipeline: dict,
    db: str,
    as_of: int | None = None,
    provenance: dict | None = None
) -> dict:
    as_of = as_of if as_of is not None else get_timestamp()
    pipelines = get_pipelines(db, pipeline)
    entries, display = {}, {}
    for pipeline in pipelines:
        if pipeline.pipeline_guid in entries:
            raise DuplicatedIdError(
                f"two versions of protocol {pipeline.pipeline_guid} in one lock; "
                "at most one version of a protocol may be active"
            )
        entries[pipeline.pipeline_guid] = {"guid": pipeline.pipeline_guid, "hash": pipeline.hash}
        display[pipeline.pipeline_guid] = {
            "title": pipeline.title,
            "dag" : pipeline.dag,
            "created_on": as_iso(pipeline.created_on) if pipeline.created_on else None,
            "creator": decode_entry(pipeline.creator),
            
        }
    document = {
        "manifest_hash": manifest_hash(entries),
        "as_of": as_iso(as_of),
        "created_at": as_iso(get_timestamp()),
        "provenance": provenance or {},
        "entries": entries,
        "pipelines": display,
    }
    
    return document
    

def generate_lock(protocol_lock:dict|None, pipeline_lock:dict|None):
    if protocol_lock and pipeline_lock:
        protocol_lock["pipeline"] = pipeline_lock
        return protocol_lock
    elif protocol_lock and not pipeline_lock:
        return protocol_lock
    elif not protocol_lock and pipeline_lock:
        return pipeline_lock
    else:
        raise ValueError("No lock files to return!")
    