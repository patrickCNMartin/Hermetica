# -----------------------------------------------------------------------------#
# TESTS — artefacts -> rows -> sqlite, and the temporal model on top
# -----------------------------------------------------------------------------#
"""The invariant everything here defends: at any instant each protocol_id has at
most one active version. That is what makes "the protocols as of date D" a single
unambiguous answer."""

import copy
import json
import sqlite3
import unicodedata

import pytest

from seal.contract import (
    METADATA_FIELDS,
    build_protocol_artefact,
    canonical_json,
    hash_bytes,
    protocol_hash,
)
from seal.dates import as_date, to_epoch
from seal.store import (
    _CONTENT_COLUMNS,
    DuplicateProtocolIdError,
    ProtocolRow,
    UnknownProtocolHashError,
    active_hashes,
    build_row,
    connect,
    diff_pull,
    format_entry,
    get_content,
    initialize_db,
    verify_protocols,
    write_pull,
)

PULLED_AT = to_epoch("2026-07-27")
LATER = to_epoch("2026-08-03")
CREATED_ON = to_epoch("2025-04-29")
FOREVER = 9223372036854775807


# -----------------------------------------------------------------------------#
# HELPERS
# -----------------------------------------------------------------------------#
@pytest.fixture
def protocol(record):
    """A by-ID record with a chosen id, so tests can build a cast of protocols."""
    def _protocol(pid: int, archetype: str = "baseline", **overrides) -> dict:
        raw = record(archetype)
        raw["id"] = pid
        raw.setdefault("created_on", CREATED_ON)
        raw["created_on"] = overrides.pop("created_on", CREATED_ON)
        raw.update(overrides)
        return raw
    return _protocol


def rows_for(protocols, pulled_at=None):
    return format_entry(
        [build_protocol_artefact(p) for p in protocols], pulled_at
    )


def query(db: str, sql: str, *params):
    with connect(db, read_only=True) as conn:
        return conn.execute(sql, params).fetchall()


def columns_of(db: str, table: str) -> list[str]:
    with connect(db, read_only=True) as conn:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def live_hashes(db: str) -> dict[str, str]:
    with connect(db, read_only=True) as conn:
        return active_hashes(conn)


def active_count(db: str) -> list[tuple]:
    """protocol_ids with more than one live version — must always be empty."""
    return query(
        db,
        "SELECT protocol_id, COUNT(*) FROM protocol_history "
        "WHERE deprecated_at IS NULL GROUP BY protocol_id HAVING COUNT(*) > 1",
    )


def overlaps(db: str) -> list[tuple]:
    """protocol_ids whose validity intervals overlap at ANY instant.

    Stronger than active_count, which only sees rows open right now: a reopened
    interval can sit inside a closed one and still make two versions resolve as
    active for a past date T.
    """
    return query(
        db,
        "SELECT a.protocol_id, a.valid_from, b.valid_from "
        "FROM protocol_history a JOIN protocol_history b "
        "  ON a.protocol_id = b.protocol_id AND a.rowid < b.rowid "
        f"WHERE a.valid_from < COALESCE(b.deprecated_at, {FOREVER}) "
        f"  AND b.valid_from < COALESCE(a.deprecated_at, {FOREVER})",
    )


# -----------------------------------------------------------------------------#
# 1. A DATABASE BEING BUILT
# -----------------------------------------------------------------------------#
class TestDatabaseBuild:
    CONTENT_COLUMNS = ["hash", "protocol_id", "protocol_guid", "title", "doi",
                       "reserved_doi", "uri", "protocol", "created_on", "creator",
                       "authors", "last_modified_on"]
    HISTORY_COLUMNS = ["protocol_id", "hash", "valid_from", "deprecated_at"]
    SNAPSHOT_COLUMNS = ["manifest_hash", "created_at", "provenance"]

    def test_tables_are_created(self, db_path):
        initialize_db(db_path)
        tables = {
            name for (name,) in query(
                db_path, "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"protocol_content", "protocol_history", "snapshots"} <= tables

    def test_old_single_table_is_gone(self, db_path):
        """protocol_versions is retired — content and history are separate now."""
        initialize_db(db_path)
        tables = {
            name for (name,) in query(
                db_path, "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "protocol_versions" not in tables

    def test_content_schema(self, db_path):
        initialize_db(db_path)
        assert columns_of(db_path, "protocol_content") == self.CONTENT_COLUMNS

    def test_history_schema(self, db_path):
        initialize_db(db_path)
        assert columns_of(db_path, "protocol_history") == self.HISTORY_COLUMNS

    def test_snapshot_schema(self, db_path):
        initialize_db(db_path)
        assert columns_of(db_path, "snapshots") == self.SNAPSHOT_COLUMNS

    def test_indexes_are_created(self, db_path):
        initialize_db(db_path)
        indexes = {
            name for (name,) in query(
                db_path, "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert {
            "idx_content_protocol_id",
            "idx_history_protocol_id",
            "idx_history_validity",
        } <= indexes

    def test_initialize_is_idempotent(self, db_path):
        """Calling twice is a harmless no-op (safe to run on every sync)."""
        initialize_db(db_path)
        initialize_db(db_path)
        assert query(db_path, "SELECT COUNT(*) FROM protocol_content") == [(0,)]


class TestColumnDerivation:
    """Columns are derived from METADATA_FIELDS, never restated — these guard it."""

    def test_content_columns_match_the_table(self, db_path):
        initialize_db(db_path)
        assert list(_CONTENT_COLUMNS) == columns_of(db_path, "protocol_content")

    def test_metadata_fields_are_all_row_fields(self):
        """A field added to METADATA_FIELDS but not ProtocolRow raises TypeError."""
        assert set(METADATA_FIELDS) <= set(ProtocolRow._fields)

    def test_row_carries_exactly_the_columns_plus_valid_from(self):
        """valid_from belongs to history and rides along unreferenced."""
        assert set(ProtocolRow._fields) == set(_CONTENT_COLUMNS) | {"valid_from"}


# -----------------------------------------------------------------------------#
# 2. CONNECTION LIFETIME
# -----------------------------------------------------------------------------#
class TestConnectionLifetime:
    @staticmethod
    def spy_on_connections(monkeypatch) -> list:
        """Record every connection sqlite3 hands out during a test."""
        opened = []
        real_connect = sqlite3.connect

        def spy(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            opened.append(conn)
            return conn

        monkeypatch.setattr(sqlite3, "connect", spy)
        return opened

    def test_every_connection_is_closed(self, db_path, monkeypatch, protocol):
        """`with sqlite3.connect(...)` commits but does NOT close — ours must."""
        opened = self.spy_on_connections(monkeypatch)

        initialize_db(db_path)
        rows = rows_for([protocol(1)])
        write_pull(db_path, rows)
        diff_pull(db_path, rows)
        verify_protocols(db_path)

        assert len(opened) == 4
        for conn in opened:
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_connection_is_closed_even_when_the_body_raises(
        self, db_path, monkeypatch
    ):
        opened = self.spy_on_connections(monkeypatch)
        initialize_db(db_path)

        with pytest.raises(RuntimeError):
            with connect(db_path):
                raise RuntimeError("boom")

        with pytest.raises(sqlite3.ProgrammingError):
            opened[-1].execute("SELECT 1")

    def test_a_failed_write_rolls_back(self, db_path):
        """The transaction is atomic: a mid-write failure stores nothing."""
        initialize_db(db_path)
        with pytest.raises(RuntimeError):
            with connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO snapshots (manifest_hash, created_at) "
                    "VALUES ('abc', 1)"
                )
                raise RuntimeError("boom")

        assert query(db_path, "SELECT COUNT(*) FROM snapshots") == [(0,)]

    def test_read_only_connection_refuses_writes(self, db_path):
        """The query port's guarantee, enforced by sqlite rather than by us."""
        initialize_db(db_path)
        with pytest.raises(sqlite3.OperationalError):
            with connect(db_path, read_only=True) as conn:
                conn.execute(
                    "INSERT INTO snapshots (manifest_hash, created_at) "
                    "VALUES ('abc', 1)"
                )

    def test_foreign_keys_are_enforced(self, db_path):
        """The FK is real: history can only reference content that exists."""
        initialize_db(db_path)
        with pytest.raises(sqlite3.IntegrityError):
            with connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO protocol_history "
                    "(protocol_id, hash, valid_from) VALUES ('1', 'nope', 1)"
                )


# -----------------------------------------------------------------------------#
# 3. DATA INSERTED CORRECTLY
# -----------------------------------------------------------------------------#
class TestDataInsertion:
    def test_rows_are_inserted(self, db_path, protocol):
        initialize_db(db_path)
        diff = write_pull(db_path, rows_for([protocol(1), protocol(2)]))

        assert diff["new"] == ["1", "2"]
        assert query(db_path, "SELECT COUNT(*) FROM protocol_content") == [(2,)]
        assert query(db_path, "SELECT COUNT(*) FROM protocol_history") == [(2,)]

    def test_inserted_values_match_the_artefact(self, db_path, protocol):
        raw = protocol(1, title="My Protocol")
        built = build_protocol_artefact(raw)
        initialize_db(db_path)
        write_pull(db_path, rows_for([raw]))

        stored = query(
            db_path,
            "SELECT protocol_id, protocol_guid, title, doi, reserved_doi, uri, "
            "created_on FROM protocol_content",
        )[0]
        assert stored == (
            "1", built.guid, "My Protocol", built.doi, built.reserved_doi,
            built.uri, CREATED_ON,
        )

    def test_denormalized_columns_come_from_the_artefact(self, db_path, protocol):
        """doi/reserved_doi/uri are hashed AND stored — a copy for querying."""
        raw = protocol(1, archetype="reserved_doi")
        built = build_protocol_artefact(raw)
        initialize_db(db_path)
        write_pull(db_path, rows_for([raw]))

        doi, reserved, uri = query(
            db_path, "SELECT doi, reserved_doi, uri FROM protocol_content"
        )[0]
        assert reserved == built.reserved_doi and reserved
        assert doi == built.doi
        assert uri == built.uri

    def test_history_points_at_stored_content(self, db_path, protocol):
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1)]))

        assert query(
            db_path,
            "SELECT COUNT(*) FROM protocol_history JOIN protocol_content "
            "USING (hash)",
        ) == [(1,)]


class TestMetadataColumns:
    def test_authors_and_creator_are_stored_as_json(self, db_path, protocol):
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1)]))

        authors, creator = query(
            db_path, "SELECT authors, creator FROM protocol_content"
        )[0]
        assert isinstance(json.loads(authors), list)
        assert isinstance(json.loads(creator), dict)

    def test_attribution_change_is_not_a_version_change(self, db_path, protocol):
        """Re-attribution is metadata, not content — no new hash."""
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1)]))

        reattributed = protocol(1, creator={"name": "B. Other",
                                            "username": "b.other"})
        assert diff_pull(db_path, rows_for([reattributed]))["unchanged"] == ["1"]

    def test_missing_metadata_is_null_not_an_error(self, db_path, protocol):
        raw = protocol(1)
        raw["authors"] = None
        raw["creator"] = None
        initialize_db(db_path)
        write_pull(db_path, rows_for([raw]))

        assert query(
            db_path, "SELECT authors, creator FROM protocol_content"
        ) == [(None, None)]

    def test_write_is_idempotent(self, db_path, protocol):
        """Re-pulling identical content is a no-op (content-hash primary key)."""
        rows = rows_for([protocol(1), protocol(2)])
        initialize_db(db_path)
        assert write_pull(db_path, rows)["new"] == ["1", "2"]

        diff = write_pull(db_path, rows)
        assert diff["unchanged"] == ["1", "2"]
        assert diff["new"] == []
        assert query(db_path, "SELECT COUNT(*) FROM protocol_history") == [(2,)]


# -----------------------------------------------------------------------------#
# 4. VALID_FROM / BACKDATING
# -----------------------------------------------------------------------------#
class TestValidFrom:
    def test_backdates_to_created_on(self, protocol):
        """A protocol authored before this store existed opens at creation."""
        row = build_row(build_protocol_artefact(protocol(1)), pulled_at=PULLED_AT)
        assert row.valid_from == CREATED_ON
        assert as_date(row.valid_from) == "2025-04-29"

    def test_falls_back_to_pull_time(self, protocol):
        """No created_on -> the interval opens when we first saw it."""
        row = build_row(
            build_protocol_artefact(protocol(1, created_on=None)),
            pulled_at=PULLED_AT,
        )
        assert row.created_on is None
        assert row.valid_from == PULLED_AT

    def test_falsy_created_on_falls_back(self, protocol):
        """created_on of 0 is not a real epoch — treat it as absent."""
        row = build_row(
            build_protocol_artefact(protocol(1, created_on=0)), pulled_at=PULLED_AT
        )
        assert row.valid_from == PULLED_AT

    def test_one_pull_shares_one_timestamp(self, protocol):
        """Every row in a batch gets the same fallback time, not per-row clocks."""
        rows = rows_for(
            [protocol(1, created_on=None), protocol(2, created_on=None)], PULLED_AT
        )
        assert {r.valid_from for r in rows} == {PULLED_AT}

    def test_dates_are_stored_as_integers(self, protocol):
        """The store holds epoch seconds only — no date strings."""
        row = build_row(build_protocol_artefact(protocol(1)), pulled_at=PULLED_AT)
        assert isinstance(row.created_on, int)
        assert isinstance(row.valid_from, int)

    def test_created_on_does_not_affect_the_stored_blob(self, protocol):
        """The blob holds identity only, so it still rehashes to its key."""
        row = build_row(build_protocol_artefact(protocol(1)))
        assert "created_on" not in json.loads(row.protocol)
        assert hash_bytes(canonical_json(json.loads(row.protocol))) == row.hash

    def test_only_the_first_version_backdates(self, db_path, protocol):
        """A later version opens at the pull, not at the protocol's birthday.

        created_on says when the PROTOCOL was authored, not when this version was
        made. Backdating a second version would reopen inside the first's interval
        and leave two versions active at once.
        """
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1, title="Original")], PULLED_AT),
                   PULLED_AT)
        write_pull(db_path, rows_for([protocol(1, title="Edited")], LATER), LATER)

        assert query(
            db_path,
            "SELECT valid_from, deprecated_at FROM protocol_history "
            "ORDER BY valid_from",
        ) == [(CREATED_ON, LATER), (LATER, None)]


# -----------------------------------------------------------------------------#
# 5. STORED BLOB AND ITS HASH AGREE
# -----------------------------------------------------------------------------#
class TestStoredHashIntegrity:
    def test_row_hash_is_the_hash_of_its_own_blob(self, protocol):
        row = build_row(build_protocol_artefact(protocol(1)))
        assert hash_bytes(row.protocol.encode("ascii")) == row.hash

    def test_row_hash_matches_the_contract_hash(self, protocol):
        """store.py and contract.py must not drift into two hash schemes."""
        built = build_protocol_artefact(protocol(1))
        assert build_row(built).hash == protocol_hash(built)

    def test_the_blob_is_serialized_once(self, protocol):
        """build_row hashes the exact bytes it stores — no re-serialization."""
        built = build_protocol_artefact(protocol(1))
        row = build_row(built)
        assert row.protocol.encode("ascii") == canonical_json(built.hashable())

    def test_stored_blob_rehashes_to_its_key(self, db_path, protocol):
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1), protocol(2)]))

        for stored_hash, blob in query(
            db_path, "SELECT hash, protocol FROM protocol_content"
        ):
            assert hash_bytes(blob.encode("ascii")) == stored_hash

    def test_verify_passes_on_untampered_db(self, db_path, protocol):
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1)]))
        assert verify_protocols(db_path) == []

    def test_verify_catches_tampering(self, db_path, protocol):
        """Editing a stored blob out from under its hash must be detectable."""
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1)]))

        with connect(db_path) as conn:
            conn.execute(
                "UPDATE protocol_content SET protocol = ?",
                ('{"id":1,"title":"TAMPERED"}',),
            )

        assert len(verify_protocols(db_path)) == 1

    def test_unicode_title_round_trips(self, db_path, protocol):
        """NFD input is normalized once, so the stored blob still verifies."""
        nfd = unicodedata.normalize("NFD", "Protocole café")
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1, title=nfd)]))
        assert verify_protocols(db_path) == []


# -----------------------------------------------------------------------------#
# 6. READING CONTENT BACK
# -----------------------------------------------------------------------------#
class TestGetContent:
    @pytest.fixture
    def stocked(self, db_path, protocol):
        initialize_db(db_path)
        rows = rows_for([protocol(1), protocol(2), protocol(3)])
        write_pull(db_path, rows)
        return db_path, [r.hash for r in rows]

    def test_returns_rows_in_the_order_asked_for(self, stocked):
        """Not the order sqlite happens to return — a lock pins an ordered set."""
        db, hashes = stocked
        wanted = [hashes[2], hashes[0], hashes[1]]
        assert [row.hash for row in get_content(db, wanted)] == wanted

    def test_an_unknown_hash_raises(self, stocked):
        """A bad pin silently shrinking a lock is the failure this guards."""
        db, hashes = stocked
        with pytest.raises(UnknownProtocolHashError, match="sha256:"):
            get_content(db, [hashes[0], "sha256:" + "0" * 64])

    def test_the_error_names_every_missing_hash(self, stocked):
        db, _ = stocked
        missing = ["sha256:" + "0" * 64, "sha256:" + "1" * 64]
        with pytest.raises(UnknownProtocolHashError) as caught:
            get_content(db, missing)
        assert all(h in str(caught.value) for h in missing)

    def test_empty_request_is_empty(self, stocked):
        db, _ = stocked
        assert get_content(db, []) == []

    def test_blob_is_omitted_when_not_asked_for(self, stocked):
        """The blob dwarfs every other column; a pin set does not need it."""
        db, hashes = stocked
        rows = get_content(db, hashes, with_blob=False)
        assert all(row.protocol is None for row in rows)
        assert all(row.protocol_id for row in rows)

    def test_blob_is_the_stored_bytes(self, stocked):
        db, hashes = stocked
        for row in get_content(db, hashes):
            assert hash_bytes(row.protocol.encode("ascii")) == row.hash

    def test_a_repeated_hash_is_returned_twice(self, stocked):
        db, hashes = stocked
        assert len(get_content(db, [hashes[0], hashes[0]])) == 2


# -----------------------------------------------------------------------------#
# 7. DETECTING WHAT CHANGED
# -----------------------------------------------------------------------------#
class TestChangeDetection:
    def test_empty_db_sees_everything_as_new(self, db_path, protocol):
        initialize_db(db_path)
        diff = diff_pull(db_path, rows_for([protocol(1), protocol(2)]))

        assert diff["new"] == ["1", "2"]
        assert diff["changed"] == []
        assert diff["unchanged"] == []
        assert diff["absent"] == []

    def test_identical_pull_is_all_unchanged(self, db_path, protocol):
        initialize_db(db_path)
        rows = rows_for([protocol(1), protocol(2)])
        write_pull(db_path, rows)

        diff = diff_pull(db_path, rows)
        assert diff["unchanged"] == ["1", "2"]
        assert diff["new"] == []
        assert diff["changed"] == []

    def test_edited_protocol_is_changed_not_new(self, db_path, protocol):
        """Same protocol_id, different content hash -> changed."""
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1, title="Original")]))

        diff = diff_pull(db_path, rows_for([protocol(1, title="Edited")]))
        assert diff["changed"] == ["1"]
        assert diff["new"] == []

    def test_request_time_noise_is_not_a_change(self, db_path, protocol):
        """The point of the allowlist: noise must not read as an edit."""
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1)]))

        noisy = protocol(1)
        noisy["stats"] = {"number_of_views": 999_999}
        noisy["image"] = {"source": "https://x.example.org/y.jpg?Policy=NEW-TOKEN"}

        diff = diff_pull(db_path, rows_for([noisy]))
        assert diff["unchanged"] == ["1"]
        assert diff["changed"] == []

    def test_dropped_protocol_is_absent(self, db_path, protocol):
        """Content-addressing cannot see absence — the id-set diff has to."""
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1), protocol(2)]))

        diff = diff_pull(db_path, rows_for([protocol(1)]))
        assert diff["absent"] == ["2"]
        assert diff["unchanged"] == ["1"]

    def test_active_hashes_ignores_deprecated(self, db_path, protocol):
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1)]))
        with connect(db_path) as conn:
            conn.execute("UPDATE protocol_history SET deprecated_at = 1")

        assert live_hashes(db_path) == {}


# -----------------------------------------------------------------------------#
# 8. THE WRITE PATH — INTERVALS MOVING
# -----------------------------------------------------------------------------#
class TestWritePath:
    def test_deprecate_on_change(self, db_path, protocol):
        """A new hash closes the prior interval and opens a new one."""
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1, title="Original")], PULLED_AT),
                   PULLED_AT)
        write_pull(db_path, rows_for([protocol(1, title="Edited")], LATER), LATER)

        history = query(
            db_path,
            "SELECT title, valid_from, deprecated_at "
            "FROM protocol_history JOIN protocol_content USING (hash) "
            "ORDER BY valid_from",
        )
        assert [h[0] for h in history] == ["Original", "Edited"]
        assert history[0][2] == LATER   # old interval closed at the pull
        assert history[1][2] is None    # new interval open

    def test_deprecate_on_absence(self, db_path, protocol):
        """A protocol that vanishes upstream is closed by the id-set diff."""
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1), protocol(2)], PULLED_AT),
                   PULLED_AT)

        diff = write_pull(db_path, rows_for([protocol(1)], LATER), LATER)

        assert diff["absent"] == ["2"]
        assert live_hashes(db_path).keys() == {"1"}
        assert query(
            db_path,
            "SELECT deprecated_at FROM protocol_history WHERE protocol_id = '2'",
        ) == [(LATER,)]

    def test_blob_survives_deprecation(self, db_path, protocol):
        """Old content stays resolvable by hash forever, so pins reproduce."""
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1), protocol(2)], PULLED_AT),
                   PULLED_AT)
        write_pull(db_path, rows_for([protocol(1, title="Edited")], LATER), LATER)

        # id-2 deprecated by absence, id-1's first version by change:
        # three distinct blobs, none deleted.
        assert query(db_path, "SELECT COUNT(*) FROM protocol_content") == [(3,)]
        assert verify_protocols(db_path) == []

    def test_deprecated_content_is_still_readable(self, db_path, protocol):
        """The claim that makes a pinned manifest reproduce years later."""
        initialize_db(db_path)
        first = rows_for([protocol(1, title="Original")], PULLED_AT)
        write_pull(db_path, first, PULLED_AT)
        write_pull(db_path, rows_for([protocol(1, title="Edited")], LATER), LATER)

        assert get_content(db_path, [first[0].hash])[0].title == "Original"

    def test_at_most_one_active_version_per_id(self, db_path, protocol):
        """The invariant that makes a named protocol resolve unambiguously."""
        initialize_db(db_path)
        for title, stamp in [("v1", PULLED_AT), ("v2", LATER), ("v3", LATER + 1)]:
            write_pull(
                db_path,
                rows_for([protocol(1, title=title), protocol(2)], stamp), stamp,
            )

        assert active_count(db_path) == []
        assert overlaps(db_path) == []
        assert len(live_hashes(db_path)) == 2

    def test_intervals_do_not_overlap(self, db_path, protocol):
        """Each interval starts exactly where the previous one closed."""
        initialize_db(db_path)
        for title, stamp in [("v1", PULLED_AT), ("v2", LATER), ("v3", LATER + 1)]:
            write_pull(db_path, rows_for([protocol(1, title=title)], stamp), stamp)

        assert query(
            db_path,
            "SELECT valid_from, deprecated_at FROM protocol_history "
            "WHERE protocol_id = '1' ORDER BY valid_from",
        ) == [(CREATED_ON, LATER), (LATER, LATER + 1), (LATER + 1, None)]

    def test_unchanged_protocol_keeps_its_original_interval(self, db_path, protocol):
        """A no-op pull must not churn history."""
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1)], PULLED_AT), PULLED_AT)
        write_pull(db_path, rows_for([protocol(1)], LATER), LATER)

        assert query(
            db_path, "SELECT valid_from, deprecated_at FROM protocol_history"
        ) == [(CREATED_ON, None)]

    def test_revert_to_previous_content_reopens_an_interval(self, db_path, protocol):
        """Undoing an edit reuses the stored blob but is a new interval."""
        initialize_db(db_path)
        original = rows_for([protocol(1, title="A")], PULLED_AT)
        write_pull(db_path, original, PULLED_AT)
        write_pull(db_path, rows_for([protocol(1, title="B")], LATER), LATER)
        diff = write_pull(
            db_path, rows_for([protocol(1, title="A")], LATER + 1), LATER + 1
        )

        assert diff["changed"] == ["1"]
        # Two blobs, three intervals: content deduped, history not.
        assert query(db_path, "SELECT COUNT(*) FROM protocol_content") == [(2,)]
        assert query(db_path, "SELECT COUNT(*) FROM protocol_history") == [(3,)]
        assert live_hashes(db_path)["1"] == original[0].hash

    def test_reappearing_protocol_opens_a_new_interval(self, db_path, protocol):
        """Absent then back: a new interval, NOT a backdate into the closed one.

        diff calls it "new" because nothing is active for that id, but it has
        history — backdating to created_on here would reopen at 2025-04-29 and two
        versions would resolve as active for every date in the gap.
        """
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1), protocol(2)], PULLED_AT),
                   PULLED_AT)
        write_pull(db_path, rows_for([protocol(1)], LATER), LATER)
        diff = write_pull(
            db_path, rows_for([protocol(1), protocol(2)], LATER + 1), LATER + 1
        )

        assert diff["new"] == ["2"]
        assert query(
            db_path,
            "SELECT valid_from, deprecated_at FROM protocol_history "
            "WHERE protocol_id = '2' ORDER BY valid_from",
        ) == [(CREATED_ON, LATER), (LATER + 1, None)]
        assert active_count(db_path) == []
        assert overlaps(db_path) == []

    def test_no_interval_overlaps_across_a_churny_history(self, db_path, protocol):
        """Edits, disappearances and returns interleaved — still unambiguous."""
        initialize_db(db_path)
        pulls = [
            ([protocol(1, title="A"), protocol(2)], PULLED_AT),
            ([protocol(1, title="B")], LATER),
            ([protocol(1, title="B"), protocol(2)], LATER + 1),
            ([protocol(2, title="C")], LATER + 2),
            ([protocol(1, title="A"), protocol(2, title="C")], LATER + 3),
        ]
        for protocols, stamp in pulls:
            write_pull(db_path, rows_for(protocols, stamp), stamp)

        assert overlaps(db_path) == []
        assert active_count(db_path) == []
        assert verify_protocols(db_path) == []

    def test_duplicate_protocol_id_in_one_pull_raises(self, db_path, protocol):
        """Two versions of one protocol in a pull would break the invariant."""
        initialize_db(db_path)
        rows = rows_for(
            [protocol(1, title="A"), protocol(1, title="B")], PULLED_AT
        )

        with pytest.raises(DuplicateProtocolIdError, match="protocol_id"):
            write_pull(db_path, rows, PULLED_AT)

        assert query(db_path, "SELECT COUNT(*) FROM protocol_content") == [(0,)]

    def test_write_returns_the_diff_it_applied(self, db_path, protocol):
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1), protocol(2)], PULLED_AT),
                   PULLED_AT)

        diff = write_pull(
            db_path,
            rows_for([protocol(1), protocol(3), protocol(2, title="Edited")], LATER),
            LATER,
        )

        assert diff == {
            "new": ["3"], "changed": ["2"], "unchanged": ["1"], "absent": [],
        }


# -----------------------------------------------------------------------------#
# 9. TITLE IS DISPLAY ONLY
# -----------------------------------------------------------------------------#
class TestTitleIsNotIdentity:
    def test_two_protocols_may_share_a_title(self, db_path, protocol):
        """Upstream really does carry duplicates — identity is the id, not the name."""
        initialize_db(db_path)
        diff = write_pull(
            db_path,
            rows_for([protocol(1, title="untitled protocol"),
                      protocol(2, title="untitled protocol")]),
        )

        assert diff["new"] == ["1", "2"]
        assert len(live_hashes(db_path)) == 2

    def test_same_content_under_different_ids_stays_two_protocols(
        self, db_path, protocol
    ):
        """Identical content, different id: two histories, two hashes."""
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1), protocol(2)]))

        hashes = live_hashes(db_path)
        assert hashes["1"] != hashes["2"]   # id is inside the hash
        assert query(db_path, "SELECT COUNT(*) FROM protocol_content") == [(2,)]

    def test_a_retitle_is_a_new_version(self, db_path, protocol):
        """title is display-only for resolving, but it IS hashed content."""
        initialize_db(db_path)
        write_pull(db_path, rows_for([protocol(1, title="Before")], PULLED_AT),
                   PULLED_AT)
        diff = write_pull(
            db_path, rows_for([protocol(1, title="After")], LATER), LATER
        )
        assert diff["changed"] == ["1"]


# -----------------------------------------------------------------------------#
# 10. THE WHOLE FIXTURE THROUGH THE WRITE PATH
# -----------------------------------------------------------------------------#
class TestEveryArchetype:
    def test_all_archetypes_write_and_verify(self, db_path, by_id_records):
        """steps:null and empty versions included — the shapes that break code."""
        initialize_db(db_path)
        rows = rows_for([copy.deepcopy(r) for r in by_id_records.values()])

        diff = write_pull(db_path, rows, PULLED_AT)

        assert len(diff["new"]) == len(by_id_records)
        assert verify_protocols(db_path) == []
        assert overlaps(db_path) == []
        assert len(live_hashes(db_path)) == len(by_id_records)

    def test_a_second_identical_pull_changes_nothing(self, db_path, by_id_records):
        initialize_db(db_path)
        records = [copy.deepcopy(r) for r in by_id_records.values()]
        write_pull(db_path, rows_for(records, PULLED_AT), PULLED_AT)

        before = query(db_path, "SELECT COUNT(*) FROM protocol_history")
        diff = write_pull(db_path, rows_for(records, LATER), LATER)

        assert len(diff["unchanged"]) == len(by_id_records)
        assert diff["changed"] == [] and diff["new"] == []
        assert query(db_path, "SELECT COUNT(*) FROM protocol_history") == before
