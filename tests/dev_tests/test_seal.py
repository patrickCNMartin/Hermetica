# -----------------------------------------------------------------------------#
# TESTS — lock documents: generate, export, verify
# -----------------------------------------------------------------------------#
"""A lock is a contract, not a document: the claim is that the file alone pins an
exact set of protocol versions and can prove it later without a database."""

import copy
import json
import os

import pytest

from seal.seal import (
    MalformedLockError,
    export_lock,
    export_pins,
    export_pipeline,
    generate_protocol_lock,
    is_verified,
    manifest_hash,
    verify_lock,
)
from seal.store import (
    DuplicateProtocolIdError,
    active_hashes,
    connect,
    format_db_entry,
    initialize_protocol_db,
    write_pull,
)
from sources.protocols_io.artefact import build_protocol_artefact
from utils.dates import to_epoch

PULLED_AT = to_epoch("2026-07-27")
LAST_YEAR = to_epoch("2025-06-01")

PINS_KEYS = {"manifest_hash", "as_of", "created_at", "provenance", "entries"}
LOCK_KEYS = PINS_KEYS | {"protocols", "bodies"}


# -----------------------------------------------------------------------------#
# HELPERS
# -----------------------------------------------------------------------------#
@pytest.fixture
def store(db_path, by_id_records):
    """A populated store plus the hashes active in it."""
    initialize_protocol_db(db_path)
    artefacts = [
        build_protocol_artefact(copy.deepcopy(r)) for r in by_id_records.values()
    ]
    write_pull(db_path, format_db_entry(artefacts, PULLED_AT), PULLED_AT)
    with connect(db_path, read_only=True) as conn:
        return db_path, list(active_hashes(conn).values())


@pytest.fixture
def lock(store):
    db, hashes = store
    return generate_protocol_lock(hashes, db, provenance={"source": "test"})


def rewrite(path, document, **kwargs):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, **kwargs)
    return path


# -----------------------------------------------------------------------------#
# 1. GENERATE
# -----------------------------------------------------------------------------#
class TestGenerate:
    def test_entries_pin_every_requested_hash(self, store, lock):
        _, hashes = store
        assert {e["hash"] for e in lock["entries"].values()} == set(hashes)

    def test_entries_are_keyed_by_protocol_id(self, lock):
        for protocol_id, entry in lock["entries"].items():
            assert isinstance(protocol_id, str)
            assert set(entry) == {"guid", "hash"}

    def test_manifest_hash_covers_entries_alone(self, store):
        """Two locks over the same pins are the same manifest, whoever made them."""
        db, hashes = store
        a = generate_protocol_lock(hashes, db, provenance={"who": "alice"})
        b = generate_protocol_lock(
            hashes, db, provenance={"who": "bob"}, as_of=LAST_YEAR
        )
        assert a["manifest_hash"] == b["manifest_hash"]
        assert a["provenance"] != b["provenance"]

    def test_manifest_hash_reproduces_from_entries(self, lock):
        assert manifest_hash(lock["entries"]) == lock["manifest_hash"]

    def test_a_different_pin_set_is_a_different_manifest(self, store, lock):
        db, hashes = store
        fewer = generate_protocol_lock(hashes[:-1], db)
        assert fewer["manifest_hash"] != lock["manifest_hash"]

    def test_as_of_and_created_at_diverge_when_backdating(self, store):
        """as_of is what the pins represent; created_at is when this ran."""
        db, hashes = store
        built = generate_protocol_lock(hashes, db, as_of=LAST_YEAR)
        assert built["as_of"].startswith("2025-06-01")
        assert not built["created_at"].startswith("2025-06-01")

    def test_as_of_defaults_to_now(self, store):
        db, hashes = store
        built = generate_protocol_lock(hashes, db)
        assert built["as_of"] == built["created_at"]

    def test_without_bodies_the_manifest_is_the_same(self, store, lock):
        """Skipping the blob column must not change what the lock pins."""
        db, hashes = store
        light = generate_protocol_lock(hashes, db, with_bodies=False)
        assert light["manifest_hash"] == lock["manifest_hash"]
        assert "bodies" not in light

    def test_display_fields_are_present_but_unhashed(self, lock):
        sample = next(iter(lock["protocols"].values()))
        assert {"title", "doi", "reserved_doi", "uri", "creator", "authors"} <= set(
            sample
        )

    def test_two_versions_of_one_protocol_raise(self, db_path, by_id_records):
        """One protocol, one active version — a lock may not violate it either."""
        initialize_protocol_db(db_path)
        record = copy.deepcopy(next(iter(by_id_records.values())))
        edited = copy.deepcopy(record)
        edited["title"] = "A different title"

        first = format_db_entry([build_protocol_artefact(record)], PULLED_AT)
        second = format_db_entry([build_protocol_artefact(edited)], PULLED_AT + 1)
        write_pull(db_path, first, PULLED_AT)
        write_pull(db_path, second, PULLED_AT + 1)

        # Both blobs are still stored; pinning both would resolve one id twice.
        with pytest.raises(DuplicateProtocolIdError, match="protocol"):
            generate_protocol_lock([first[0].hash, second[0].hash], db_path)


# -----------------------------------------------------------------------------#
# 2. EXPORT — does the file hold what it claims
# -----------------------------------------------------------------------------#
class TestExport:
    def test_export_pins_writes_only_the_pin_set(self, lock, tmp_path):
        path = str(tmp_path / "pins.json")
        export_pins(lock, path)
        written = json.loads(open(path, encoding="utf-8").read())

        assert set(written) == PINS_KEYS
        assert "bodies" not in written and "protocols" not in written

    def test_export_lock_writes_pins_display_and_bodies(self, lock, tmp_path):
        path = str(tmp_path / "lock.json")
        export_lock(lock, path)
        written = json.loads(open(path, encoding="utf-8").read())

        assert set(written) == LOCK_KEYS

    def test_every_pin_has_a_body(self, lock, tmp_path):
        path = str(tmp_path / "lock.json")
        written = export_lock(lock, path)

        pinned = {entry["hash"] for entry in written["entries"].values()}
        assert set(written["bodies"]) == pinned

    def test_a_body_is_the_stored_blob(self, lock):
        from utils.hashing import canonical_json, hash_bytes

        for stored_hash, body in lock["bodies"].items():
            assert hash_bytes(canonical_json(body)) == stored_hash

    def test_export_lock_refuses_a_bodiless_build(self, store, tmp_path):
        """Better to refuse than to write a file that cannot reproduce."""
        db, hashes = store
        light = generate_protocol_lock(hashes, db, with_bodies=False)

        with pytest.raises(ValueError, match="export_pins"):
            export_lock(light, str(tmp_path / "nope.json"))

    def test_export_pipeline_rejects_a_graph(self, lock, tmp_path):
        """The DAG document shape is a Phase 3 decision, not a silent default."""
        with pytest.raises(NotImplementedError, match="DAG"):
            export_pipeline(lock, str(tmp_path / "p.json"), graph={"nodes": []})

    def test_the_file_is_valid_utf8_json_ending_in_a_newline(self, lock, tmp_path):
        path = str(tmp_path / "lock.json")
        export_lock(lock, path)
        text = open(path, encoding="utf-8").read()

        assert text.endswith("\n")
        assert json.loads(text)["manifest_hash"] == lock["manifest_hash"]

    def test_the_document_round_trips_unchanged(self, lock, tmp_path):
        path = str(tmp_path / "lock.json")
        written = export_lock(lock, path)
        assert json.loads(open(path, encoding="utf-8").read()) == written


# -----------------------------------------------------------------------------#
# 3. VERIFY
# -----------------------------------------------------------------------------#
class TestVerify:
    def test_a_fresh_lock_verifies(self, lock, tmp_path):
        path = str(tmp_path / "lock.json")
        export_lock(lock, path)
        assert is_verified(verify_lock(path))

    def test_a_fresh_pins_file_verifies(self, lock, tmp_path):
        path = str(tmp_path / "pins.json")
        export_pins(lock, path)
        assert is_verified(verify_lock(path))

    def test_a_pins_file_is_not_missing_its_bodies(self, lock, tmp_path):
        """It never claimed to carry content — absence is the format, not drift."""
        path = str(tmp_path / "pins.json")
        export_pins(lock, path)
        assert verify_lock(path)["missing_bodies"] == []

    def test_reformatting_does_not_break_a_lock(self, lock, tmp_path):
        """Only `entries` is load-bearing; the file layout is free to change."""
        path = str(tmp_path / "lock.json")
        export_lock(lock, path)
        document = json.loads(open(path, encoding="utf-8").read())

        rewritten = rewrite(
            str(tmp_path / "reformatted.json"),
            document,
            indent=8,
            sort_keys=False,
            separators=(",", ": "),
        )
        assert is_verified(verify_lock(rewritten))

    def test_provenance_and_timestamps_are_outside_the_hash(self, lock, tmp_path):
        path = str(tmp_path / "lock.json")
        document = export_lock(lock, path)
        document["provenance"] = {"who": "someone else entirely"}
        document["created_at"] = "1999-01-01T00:00:00+00:00"

        assert is_verified(verify_lock(rewrite(str(tmp_path / "p.json"), document)))

    def test_verify_needs_no_database(self, lock, store, tmp_path):
        """The whole claim of export_lock: the file reproduces on its own."""
        db, _ = store
        path = str(tmp_path / "lock.json")
        export_lock(lock, path)

        os.remove(db)
        assert not os.path.exists(db)
        assert is_verified(verify_lock(path))


class TestTamperDetection:
    @pytest.fixture
    def written(self, lock, tmp_path):
        path = str(tmp_path / "lock.json")
        export_lock(lock, path)
        return path, json.loads(open(path, encoding="utf-8").read()), tmp_path

    def test_a_changed_pin_is_caught(self, written):
        _, document, tmp_path = written
        pid = sorted(document["entries"])[0]
        document["entries"][pid]["hash"] = "sha256:" + "0" * 64

        drift = verify_lock(rewrite(str(tmp_path / "t.json"), document))
        assert drift["manifest_hash"]
        assert not is_verified(drift)

    def test_a_removed_entry_is_caught(self, written):
        _, document, tmp_path = written
        document["entries"].pop(sorted(document["entries"])[0])

        drift = verify_lock(rewrite(str(tmp_path / "t.json"), document))
        assert drift["manifest_hash"]

    def test_an_added_entry_is_caught(self, written):
        _, document, tmp_path = written
        document["entries"]["999999"] = {"guid": "X", "hash": "sha256:" + "0" * 64}

        drift = verify_lock(rewrite(str(tmp_path / "t.json"), document))
        assert drift["manifest_hash"]
        assert drift["missing_bodies"] == ["sha256:" + "0" * 64]

    def test_a_mutated_body_is_caught_and_named(self, written):
        """The manifest still matches — only re-hashing the blob finds this."""
        _, document, tmp_path = written
        target = sorted(document["bodies"])[0]
        document["bodies"][target]["title"] = "TAMPERED"

        drift = verify_lock(rewrite(str(tmp_path / "t.json"), document))
        assert drift["manifest_hash"] == []
        assert drift["body_hash"] == [target]

    def test_a_deleted_body_is_caught(self, written):
        _, document, tmp_path = written
        target = sorted(document["bodies"])[0]
        del document["bodies"][target]

        drift = verify_lock(rewrite(str(tmp_path / "t.json"), document))
        assert drift["missing_bodies"] == [target]

    def test_an_unpinned_body_is_caught(self, written):
        """Content smuggled in outside the manifest is not part of the contract."""
        _, document, tmp_path = written
        document["bodies"]["sha256:" + "1" * 64] = {"smuggled": True}

        drift = verify_lock(rewrite(str(tmp_path / "t.json"), document))
        assert "sha256:" + "1" * 64 in drift["orphan_bodies"]

    def test_a_file_that_is_not_a_lock_raises(self, tmp_path):
        path = rewrite(str(tmp_path / "junk.json"), {"hello": "world"})
        with pytest.raises(MalformedLockError, match="manifest_hash"):
            verify_lock(path)
