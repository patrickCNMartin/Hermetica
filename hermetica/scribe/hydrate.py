# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import json

from seal.seal import generate_protocol_lock, verify_lock
from utils.dates import to_epoch


class LockDriftError(ValueError):
    """The lock file does not verify against its own bytes."""

    def __init__(self, path: str, drift: dict[str, list[str]]):
        self.path, self.drift = path, drift
        super().__init__(
            f"{path} does not verify, refusing to hydrate: "
            f"{ {key: value for key, value in drift.items() if value} }"
        )


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
    if any(drift.values()):
        raise LockDriftError(path, drift)

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
    # No cross-check on the rebuilt manifest_hash: protocol_uid is
    # f"{source}:{id}" and source, id and guid are all hashed, so every field in
    # `entries` is a function of the hash. An honest store cannot rebuild them
    # differently, and a dishonest one is refused by the content triggers.
    return lock
