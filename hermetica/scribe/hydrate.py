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


class ManifestMismatchError(ValueError):
    """The pins resolved, but the store holds a different identity for them.

    Not a LockDriftError: that file verified against its own bytes. Nothing is
    corrupt — the store moved underneath a lock that is still internally sound.
    """

    def __init__(self, path: str, rebuilt: str, recorded: str):
        self.path, self.rebuilt, self.recorded = path, rebuilt, recorded
        super().__init__(
            f"rebuilt manifest {rebuilt} does not match {recorded} recorded in {path}"
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

    # The pins resolved, but the store could still hold a different protocol_id or
    # guid for one of those hashes — that document would mean something else.
    if lock["manifest_hash"] != document["manifest_hash"]:
        raise ManifestMismatchError(
            path, lock["manifest_hash"], document["manifest_hash"]
        )
    return lock
