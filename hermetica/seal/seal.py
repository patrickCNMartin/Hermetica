# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import json
from collections.abc import Iterable
from typing import Any

from seal.contract import canonical_json, hash_bytes
from seal.dates import as_iso, get_timestamp
from seal.store import DuplicateProtocolIdError, get_content

# -----------------------------------------------------------------------------#
# LOCK DOCUMENT
# -----------------------------------------------------------------------------#
# manifest_hash covers `entries` alone — the rest of the document is display.
_PINS_KEYS: tuple[str, ...] = (
    "manifest_hash",
    "as_of",
    "created_at",
    "provenance",
    "entries",
)
_LOCK_KEYS: tuple[str, ...] = _PINS_KEYS + ("protocols", "bodies")


def _decode(value: str | None) -> Any:
    """Reverse of store._as_column for the JSON-encoded metadata columns."""
    return None if value is None else json.loads(value)


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
            raise DuplicateProtocolIdError(
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
            "last_modified_on": (
                as_iso(row.last_modified_on) if row.last_modified_on else None
            ),
            "authors": _decode(row.authors),
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


# -----------------------------------------------------------------------------#
# EXPORT
# -----------------------------------------------------------------------------#
# Written human-readable, not as canonical bytes: verification re-canonicalizes
# `entries` and re-hashes, so file layout can change without invalidating a lock.
def _write(lock: dict, keys: Iterable[str], path: str) -> dict:
    document = {key: lock[key] for key in keys}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    return document


def export_pins(lock: dict, path: str) -> dict:
    """Minimal: the envelope and the pin set, no protocol content."""
    return _write(lock, _PINS_KEYS, path)


def export_lock(lock: dict, path: str) -> dict:
    """Default: pins plus display fields and full bodies — reproduces with no DB."""
    if "bodies" not in lock:
        raise ValueError(
            "lock was built with with_bodies=False; a lock without bodies "
            "cannot reproduce without the database — use export_pins"
        )
    return _write(lock, _LOCK_KEYS, path)


def export_pipeline(lock: dict, path: str, graph: dict | None = None) -> dict:
    """Pipeline flavour — structural hook only; the DAG shape is not settled."""
    if graph is not None:
        raise NotImplementedError(
            "pipeline export needs the transmute DAG document shape (Phase 3)"
        )
    return export_lock(lock, path)


# -----------------------------------------------------------------------------#
# VERIFY
# -----------------------------------------------------------------------------#
class MalformedLockError(ValueError):
    """The file is not a lock document — a key the format requires is missing."""


_DRIFT: tuple[str, ...] = (
    "manifest_hash",
    "body_hash",
    "missing_bodies",
    "orphan_bodies",
)


def verify_lock(path: str) -> dict[str, list[str]]:
    """Re-derive a lock file's hashes and report every disagreement.

    Empty lists mean verified, matching store.verify_protocols — a verifier that
    raised on the first problem could not report the whole picture. Body checks
    only apply when the document carries `bodies`: a pins-only file never claimed
    to hold content, so its absence is the format, not drift.
    """
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)

    missing = [key for key in ("manifest_hash", "entries") if key not in document]
    if missing:
        raise MalformedLockError(f"not a lock document: missing {', '.join(missing)}")

    drift: dict[str, list[str]] = {key: [] for key in _DRIFT}
    entries = document["entries"]

    recomputed = manifest_hash(entries)
    if recomputed != document["manifest_hash"]:
        drift["manifest_hash"].append(
            f"recorded {document['manifest_hash']}, recomputed {recomputed}"
        )

    if "bodies" not in document:
        return drift

    bodies = document["bodies"]
    for stored_hash, body in sorted(bodies.items()):
        if hash_bytes(canonical_json(body)) != stored_hash:
            drift["body_hash"].append(stored_hash)

    pinned = {entry["hash"] for entry in entries.values()}
    drift["missing_bodies"] = sorted(pinned - set(bodies))
    drift["orphan_bodies"] = sorted(set(bodies) - pinned)
    return drift


def is_verified(drift: dict[str, list[str]]) -> bool:
    """True when verify_lock found nothing."""
    return not any(drift.values())
