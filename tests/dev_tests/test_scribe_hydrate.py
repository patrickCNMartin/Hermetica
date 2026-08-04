# -----------------------------------------------------------------------------#
# TESTS — a pins-only lock read back into a full lock document
# -----------------------------------------------------------------------------#
"""A pins file names hashes and carries no content. Only the store can honour it:
protocols.io serves a protocol's current version only, so a historical pin is
unfetchable upstream."""

import copy
import json

import pytest

from scribe.hydrate import LockDriftError, hydrate_pins
from seal.contract import build_protocol_artefact
from seal.dates import to_epoch
from seal.seal import export_pins, generate_protocol_lock
from seal.store import (
    UnknownProtocolHashError,
    active_hashes,
    connect,
    format_entry,
    initialize_db,
    write_pull,
)

PULLED_AT = to_epoch("2026-07-27")


@pytest.fixture
def store(db_path, by_id_records):
    """A populated store plus the hashes active in it."""
    initialize_db(db_path)
    artefacts = [
        build_protocol_artefact(copy.deepcopy(r)) for r in by_id_records.values()
    ]
    write_pull(db_path, format_entry(artefacts, PULLED_AT), PULLED_AT)
    with connect(db_path, read_only=True) as conn:
        return db_path, list(active_hashes(conn).values())


@pytest.fixture
def pins_file(store, tmp_path):
    """A pins-only lock on disk, and the DB it was pinned from."""
    db, hashes = store
    lock = generate_protocol_lock(
        hashes, db, as_of=PULLED_AT, provenance={"who": "test"}
    )
    path = str(tmp_path / "test.pins.lock")
    export_pins(lock, path)
    return path, db, lock


# -----------------------------------------------------------------------------#
# 1. ROUND TRIP
# -----------------------------------------------------------------------------#
class TestHydrate:
    def test_pins_gain_bodies(self, pins_file):
        path, db, original = pins_file
        hydrated = hydrate_pins(path, db)
        assert "bodies" not in json.loads(open(path).read())
        assert len(hydrated["bodies"]) == len(original["entries"])

    def test_the_manifest_is_preserved(self, pins_file):
        """Same pins in, same level-2 identity out."""
        path, db, original = pins_file
        assert hydrate_pins(path, db)["manifest_hash"] == original["manifest_hash"]

    def test_entries_are_unchanged(self, pins_file):
        path, db, original = pins_file
        assert hydrate_pins(path, db)["entries"] == original["entries"]

    def test_as_of_carries_over_but_created_at_is_new(self, pins_file):
        """as_of is what the pins represent; created_at is when this ran."""
        path, db, original = pins_file
        hydrated = hydrate_pins(path, db)
        assert hydrated["as_of"] == original["as_of"]
        assert "hydrated_from" in hydrated["provenance"]

    def test_original_provenance_is_kept(self, pins_file):
        path, db, _ = pins_file
        assert hydrate_pins(path, db)["provenance"]["who"] == "test"

    def test_bodies_hash_to_their_pins(self, pins_file):
        """The point of hydrating: the result must verify like any other lock."""
        from seal.contract import canonical_json, hash_bytes

        path, db, _ = pins_file
        hydrated = hydrate_pins(path, db)
        for stored_hash, body in hydrated["bodies"].items():
            assert hash_bytes(canonical_json(body)) == stored_hash


# -----------------------------------------------------------------------------#
# 2. WHAT IT REFUSES
# -----------------------------------------------------------------------------#
class TestHydrateRefuses:
    def test_a_pin_absent_from_the_store_raises(self, pins_file):
        path, db, _ = pins_file
        document = json.loads(open(path).read())
        document["entries"]["999999"] = {"guid": "X", "hash": "sha256:" + "0" * 64}
        # Rewrite the manifest too, so this fails on the missing pin, not on drift.
        from seal.seal import manifest_hash

        document["manifest_hash"] = manifest_hash(document["entries"])
        with open(path, "w") as handle:
            json.dump(document, handle)
        with pytest.raises(UnknownProtocolHashError):
            hydrate_pins(path, db)

    def test_a_tampered_pin_set_is_refused_before_any_db_read(self, pins_file):
        """verify_lock runs first: a file that does not verify is not hydrated."""
        path, db, _ = pins_file
        document = json.loads(open(path).read())
        document["manifest_hash"] = "sha256:" + "0" * 64
        with open(path, "w") as handle:
            json.dump(document, handle)
        with pytest.raises(LockDriftError):
            hydrate_pins(path, db)

    def test_a_store_disagreeing_about_guid_is_caught(self, pins_file):
        """The hashes all resolve, but to a different protocol identity."""
        path, db, _ = pins_file
        with connect(db) as conn:
            conn.execute(
                "UPDATE protocol_content SET protocol_guid = 'TAMPERED' "
                "WHERE hash = (SELECT hash FROM protocol_content LIMIT 1)"
            )
        with pytest.raises(LockDriftError, match="does not match"):
            hydrate_pins(path, db)
