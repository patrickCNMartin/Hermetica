# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import json
from collections.abc import Iterable

from compose.store import get_pipelines
from seal.store import get_protocols
from utils.constants import DRIFT, LOCK_KEYS, PINS_KEYS, PIPELINE_KEYS, PROTOCOL_KEYS
from utils.dates import as_iso, get_timestamp
from utils.hashing import decode_entry, hash_of


# -----------------------------------------------------------------------------#
# Error handling
# -----------------------------------------------------------------------------#
class DuplicatedIdError(ValueError):
    """Duplicated entries in the version control data base"""

class MalformedLockError(ValueError):
    """The file is not a lock document — a key the format requires is missing."""
# -----------------------------------------------------------------------------#
# LOCK DOCUMENT
# -----------------------------------------------------------------------------#

def generate_protocol_lock(
    protocols: Iterable[str],
    db: str,
    as_of: int | None = None,
    provenance: dict | None = None,
    with_bodies: bool = True,
) -> dict:
    """Build the lock document for an already-resolved set of protocol hashes."""
    as_of = as_of if as_of is not None else get_timestamp()
    protocols = get_protocols(db, protocols, with_blob=with_bodies)

    entries, display, bodies = {}, {}, {}
    for protocol in protocols:
        if protocol.protocol_uid in entries:
            raise DuplicatedIdError(
                f"two versions of protocol {protocol.protocol_uid} in one lock; "
                "at most one version of a protocol may be active"
            )
        entries[protocol.protocol_uid] = {
            "guid": protocol.protocol_guid,
            "hash": protocol.hash,
        }
        display[protocol.protocol_uid] = {
            "source": protocol.source,
            "protocol_id": protocol.protocol_id,
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
        "manifest_hash": hash_of(entries),
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
    hashes: Iterable[str],
    db: str,
    as_of: int | None = None,
    provenance: dict | None = None,
) -> dict:
    """Build the lock document for an already-resolved set of pipeline hashes."""
    as_of = as_of if as_of is not None else get_timestamp()

    entries, display = {}, {}
    for pipeline in get_pipelines(db, hashes):
        if pipeline.pipeline_guid in entries:
            raise DuplicatedIdError(
                f"two versions of pipeline {pipeline.pipeline_guid} in one lock; "
                "at most one version of a pipeline may be active"
            )
        entries[pipeline.pipeline_guid] = {
            "guid": pipeline.pipeline_guid,
            "hash": pipeline.hash,
        }
        display[pipeline.pipeline_guid] = {
            "title": pipeline.title,
            "dag": pipeline.DAG,
            "created_on": as_iso(pipeline.created_on) if pipeline.created_on else None,
            "creator": decode_entry(pipeline.creator),
        }
    document = {
        "manifest_hash": hash_of(entries),
        "as_of": as_iso(as_of),
        "created_at": as_iso(get_timestamp()),
        "provenance": provenance or {},
        "entries": entries,
        "pipelines": display,
    }

    return document


def generate_lock(protocol_lock: dict | None, pipeline_lock: dict | None) -> dict:
    """Merge whichever locks were built. The graph rides under `pipeline`."""
    if not (protocol_lock or pipeline_lock):
        raise ValueError("No lock files to return!")
    if protocol_lock and pipeline_lock:
        return {**protocol_lock, "pipeline": pipeline_lock}
    return protocol_lock or pipeline_lock

# -----------------------------------------------------------------------------#
# EXPORT AND SAVE LOCKS
# -----------------------------------------------------------------------------#

def write_lock_file(lock: dict, keys: Iterable[str], path: str) -> dict:
    """Write the keys this lock actually carries. `pipeline` and `dag` are
    present only on a merged or pipeline lock, so selection is by presence."""
    document = {key: lock[key] for key in keys if key in lock}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    return document


def export_pins(lock: dict, path: str) -> dict:
    """Export the pin set alone — no display fields, no bodies."""
    return write_lock_file(lock, PINS_KEYS, path)


def export_protocols(lock: dict, path: str) -> dict:
    """Export the protocol display and bodies."""
    return write_lock_file(lock, PROTOCOL_KEYS, path)


def export_pipeline(lock: dict, path: str) -> dict:
    """Export the pinned graph."""
    return write_lock_file(lock, PIPELINE_KEYS, path)


def export_lock(lock: dict, path: str) -> dict:
    """Export the whole self-contained lock — it must be able to reproduce."""
    if "bodies" not in lock:
        raise ValueError(
            "lock was built with_bodies=False and cannot reproduce; "
            "use export_pins for a pins-only file"
        )
    return write_lock_file(lock, LOCK_KEYS, path)


# -----------------------------------------------------------------------------#
# VERIFY LOCKS
# -----------------------------------------------------------------------------#


def verify_lock(path: str) -> dict[str, list[str]]:
    """Re-derive a lock file's hashes and report every disagreement.

    Empty lists mean verified, matching utils.store.verify_blobs — a verifier that
    raised on the first problem could not report the whole picture. Body checks
    only apply when the document carries `bodies`: a pins-only file never claimed
    to hold content, so its absence is the format, not drift.
    """
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)

    missing = [key for key in ("manifest_hash", "entries") if key not in document]
    if missing:
        raise MalformedLockError(f"not a lock document: missing {', '.join(missing)}")

    entries = document["entries"]
    drift: dict[str, list[str]] = {key: [] for key in DRIFT}

    recomputed = hash_of(entries)
    if recomputed != document["manifest_hash"]:
        drift["manifest_hash"] = [recomputed]

    if "bodies" not in document:
        return drift

    bodies = document["bodies"]
    pinned = {entry["hash"] for entry in entries.values()}
    drift["body_hash"] = sorted(h for h, body in bodies.items() if hash_of(body) != h)
    drift["missing_bodies"] = sorted(pinned - set(bodies))
    drift["orphan_bodies"] = sorted(set(bodies) - pinned)
    return drift
