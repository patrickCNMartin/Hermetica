# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import json
from collections.abc import Iterable

from utils.constants import LOCK_KEYS, PINS_KEYS, PIPELINE_KEYS, PROTOCOL_KEYS

# This is a seperate file because I am going to pull from
# protocols and pipelines
# The idea is that could build lock files for either or both
# I can see this being the case if you only have a sinlge protocol,
# A user might not create a pipeline for just one protocol but will still
# want to have version control for that project
# Worth noting that compose will call this lock file generation for
# specific pipeline

# -----------------------------------------------------------------------------#
# WRITE AND EXPORT LOCKS
# -----------------------------------------------------------------------------#


def write_lock_file(lock: dict, keys: Iterable[str], path: str) -> dict:
    document = {key: lock[key] for key in keys}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    return document


def export_pins(lock: dict, path: str, keys: Iterable[str] = PINS_KEYS) -> dict:
    """Export only hashes"""
    return write_lock_file(lock, keys, path)


def export_protocols(
    lock: dict, path: str, keys: Iterable[str] = PROTOCOL_KEYS
) -> dict:
    """Export Protocols only"""
    return write_lock_file(lock, keys, path)


def export_pipeline(lock: dict, path: str, keys: Iterable[str] = PIPELINE_KEYS) -> dict:
    """Export Pipeline Only"""
    return write_lock_file(lock, keys, path)


def export_lock(lock: dict, path: str, keys: Iterable[str] = LOCK_KEYS) -> dict:
    return write_lock_file(lock, keys, path)


# -----------------------------------------------------------------------------#
# VERIFY
# -----------------------------------------------------------------------------#


# # when is this useful again?
# def verify_lock(path: str) -> dict[str, list[str]]:
#     """Re-derive a lock file's hashes and report every disagreement.

#     Empty lists mean verified, matching utils.store.verify_blobs — a verifier that
#     raised on the first problem could not report the whole picture. Body checks
#     only apply when the document carries `bodies`: a pins-only file never claimed
#     to hold content, so its absence is the format, not drift.
#     """
#     with open(path, encoding="utf-8") as handle:
#         document = json.load(handle)

#     missing = [key for key in ("manifest_hash", "entries") if key not in document]
#     if missing:
#         raise MalformedLockError(f"not a lock document: missing {', '.join(missing)}")

#     drift: dict[str, list[str]] = {key: [] for key in DRIFT}
#     entries = document["entries"]

#     recomputed = manifest_hash(entries)
#     if recomputed != document["manifest_hash"]:
#         drift["manifest_hash"].append(
#             f"recorded {document['manifest_hash']}, recomputed {recomputed}"
#         )

#     if "bodies" not in document:
#         return drift

#     bodies = document["bodies"]
#     for stored_hash, body in sorted(bodies.items()):
#         if hash_bytes(canonical_json(body)) != stored_hash:
#             drift["body_hash"].append(stored_hash)

#     pinned = {entry["hash"] for entry in entries.values()}
#     drift["missing_bodies"] = sorted(pinned - set(bodies))
#     drift["orphan_bodies"] = sorted(set(bodies) - pinned)
#     return drift


# def is_verified(drift: dict[str, list[str]]) -> bool:
#     """True when verify_lock found nothing."""
#     return not any(drift.values())
