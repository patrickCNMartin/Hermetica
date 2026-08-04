# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import json

from seal.dates import to_epoch
from seal.seal import generate_protocol_lock, is_verified, verify_lock


class LockDriftError(ValueError):
    """The store disagrees with the pin set the lock file records."""


# -----------------------------------------------------------------------------#
# HYDRATE
# -----------------------------------------------------------------------------#
def hydrate_pins(path: str, db: str) -> dict:
    """Read a pins-only lock back into a full lock document, from the store.

    The DB is the only possible source: protocols.io serves a protocol's current
    version only, so a historical pin is unfetchable upstream, and a re-pull that
    did return something would defeat the pin it was meant to honour.
    """
    drift = verify_lock(path)
    if not is_verified(drift):
        raise LockDriftError(
            f"{path} does not verify, refusing to hydrate: "
            f"{ {key: value for key, value in drift.items() if value} }"
        )

    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)

    entries = document["entries"]
    as_of = document.get("as_of")
    lock = generate_protocol_lock(
        [entries[pid]["hash"] for pid in sorted(entries)],
        db,
        as_of=to_epoch(as_of) if as_of else None,
        provenance={
            **(document.get("provenance") or {}),
            "hydrated_from": path,
            "source_created_at": document.get("created_at"),
        },
    )

    # The pins resolved, but the store could still hold a different protocol_id or
    # guid for one of those hashes — that document would mean something else.
    if lock["manifest_hash"] != document["manifest_hash"]:
        raise LockDriftError(
            f"rebuilt manifest {lock['manifest_hash']} does not match "
            f"{document['manifest_hash']} recorded in {path}"
        )
    return lock
