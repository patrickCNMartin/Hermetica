# -----------------------------------------------------------------------------#
# TESTS — protocols.io pull → strip/hash → sqlite pipeline
# -----------------------------------------------------------------------------#
import json
import sqlite3
import unicodedata

import pytest
import responses

from chronos.utils.db import (
    diff_pull,
    get_active_hashes,
    initialize_db,
    insert_protocols,
    to_row,
    to_rows,
    verify_protocols,
)
from chronos.utils.request_utils import (
    IncompletePullError,
    get_protocol_list,
    process_protocols,
)
from seal.canonical import content_hash
from seal.contract import METADATA_FIELDS as SOURCE_METADATA_FIELDS
from seal.contract import STABLE_FIELDS as SOURCE_STABLE_FIELDS
from seal.contract import (
    MissingStableFieldsError,
    hashable_content,
    protocol_hash,
    select_protocol,
)
from seal.dates import as_date, to_epoch

# -----------------------------------------------------------------------------#
# CONSTANTS / FIXTURES
# -----------------------------------------------------------------------------#
BASE_URL = "https://api.protocols.io"
HEADERS = {"Authorization": "Bearer test-token"}
PROTOCOLS_URL = f"{BASE_URL}/v3/protocols"

# Fields that identify a protocol and are the ONLY input to the content hash.
# We explicitely call which fields we want to avoid pulling anything unrequired
# and to make sure that it is stable should things change.
# we want the system to fail if fields change since that could lead to downstream bugs
# created_on could be removed. It also means that if there is
# another system that is not protocols.io we can pull relevant fields
# Could use metaSolid for this I guess...
#
# Restated here as a literal on purpose: this is the spec, and
# test_source_contract_matches_spec asserts the implementation still matches it.
# Editing one without the other must fail — changing this list re-hashes every
# protocol version in the store.
STABLE_FIELDS = ["id", "guid", "title","description","doi",
                 "uri","guidelines","materials","materials_text","units","warning"]

# Pulled and stored, but deliberately NOT hashed. created_on describes when the
# protocol was authored, not what it says — it backdates valid_from so protocols
# that predate this store still resolve for earlier dates. authors/creator are
# attribution, kept for the lock file.
METADATA_FIELDS = ["created_on", "authors", "creator"]




def make_protocol(pid: int, title: str = "Test Protocol", **overrides) -> dict:
    """Build a realistic protocol record: stable fields + volatile noise.

    `image`/`versions` carry a fake signed-URL token so tests can prove the
    hash is invariant to it.
    """
    protocol = {
        # --- stable, semantic content ---
        "id": pid,
        "guid": f"{pid:032X}",
        "doi" :"",
        "title": title,
        "created_on": 1745934254,  # unix epoch -> 2025-04-29
        "uri": f"test-protocol-{pid}",
        "description" : "Some protocol" ,
        "guidelines" : '{"blocks":[{"key":"a1","text":"Wear gloves."}]}',
        "materials" : [],
        "materials_text" : "This is how you do this protocol",
        "units" : [{"unit" : "L","used": "yeah"}],
        "warning" : "null", # Not sure if this is needed yet.
        # --- metadata: stored, never hashed ---
        "authors": [{"name": "A. Researcher", "affiliation": "KI"}],
        "creator": {"name": "A. Researcher", "username": "aresearcher"},
        # --- volatile / request-time noise (must be stripped) ---
        "stats": {"number_of_views": pid * 10},
        "image": {"placeholder": f"https://files.x/y.jpg?Policy=SIGNED-{pid}"},
        "versions": [{"id": pid, "image": {"source": f"?Policy=SIGNED-{pid}"}}],
        "published_on": None,
        "public": 0,
        "peer_reviewed": 0,
    }
    protocol.update(overrides)
    return protocol


@pytest.fixture
def db_path(tmp_path) -> str:
    """A throwaway sqlite file path unique to each test."""
    return str(tmp_path / "protocol_version_control.db")


# -----------------------------------------------------------------------------#
# 1. HITTING THE SERVER
# -----------------------------------------------------------------------------#
class TestServerConnection:
    @responses.activate
    def test_request_is_made_to_protocols_endpoint(self):
        """A pull GETs the URL it was handed and returns the items."""
        responses.add(
            responses.GET, PROTOCOLS_URL,
            json={"items": [make_protocol(1)]}, status=200,
        )
        # Check that function return a list of protcols
        result = get_protocol_list(PROTOCOLS_URL, HEADERS)

        assert len(responses.calls) == 1
        assert responses.calls[0].request.url.startswith(PROTOCOLS_URL)
        assert len(result) == 1
        assert result[0]["id"] == 1

    @responses.activate
    def test_any_endpoint_can_be_targeted(self):
        """No path is baked in — a mirror or a different API version just works."""
        other = "https://mirror.example.org/v4/protocol-list"
        responses.add(responses.GET, other, json={"items": [make_protocol(1)]})

        result = get_protocol_list(other, HEADERS)

        assert responses.calls[0].request.url.startswith(other)
        assert len(result) == 1

    @responses.activate
    def test_caller_params_are_sent(self):
        """Query params come from the caller, not from inside the function."""
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": []})

        get_protocol_list(
            PROTOCOLS_URL, HEADERS,
            filter="public", order_field="date", peer_reviewed=1,
        )

        url = responses.calls[0].request.url
        assert "filter=public" in url
        assert "order_field=date" in url
        assert "peer_reviewed=1" in url

    @responses.activate
    def test_no_params_are_invented(self):
        """A bare call sends pagination only — no hardcoded scope leaks in."""
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": []})

        get_protocol_list(PROTOCOLS_URL, HEADERS)

        sent = responses.calls[0].request.params
        assert set(sent) == {"page_size", "page_id"}

    @responses.activate
    def test_start_page_is_caller_controlled(self):
        """page_id sets where the walk starts, so a pull can be resumed."""
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": []})

        get_protocol_list(PROTOCOLS_URL, HEADERS, page_id=7)

        assert "page_id=7" in responses.calls[0].request.url

# -----------------------------------------------------------------------------#
# 2. PAGES BEING PROCESSED
# -----------------------------------------------------------------------------#
class TestPagination:
    @responses.activate
    def test_walks_multiple_pages(self):
        """A full page (== page_size) triggers a next fetch; a short page stops."""
        page_1 = [make_protocol(i) for i in range(10)]   # full page -> continue
        page_2 = [make_protocol(i) for i in range(10, 13)]  # short page -> stop
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": page_1})
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": page_2})

        result = get_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10)

        assert len(responses.calls) == 2
        assert len(result) == 13
        # The second request advanced page_id (0-indexed: 0 then 1).
        assert "page_id=1" in responses.calls[1].request.url

    @responses.activate
    def test_stops_on_empty_page(self):
        """An empty items list ends pagination without error."""
        page_1 = [make_protocol(i) for i in range(10)]
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": page_1})
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": []})

        result = get_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10)

        assert len(responses.calls) == 2
        assert len(result) == 10

    @responses.activate
    def test_stops_at_max_page(self):
        """max_pull caps the number of pages fetched, regardless of server."""
        page = [make_protocol(i) for i in range(10)]
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": page})  # repeats for every request

        results = get_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10, max_pull=5)
        assert len(responses.calls) == 5
        assert len(results) == 50

        before = len(responses.calls)
        results = get_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10, max_pull=3)
        assert len(responses.calls) - before == 3
        assert len(results) == 30

    @responses.activate
    def test_default_has_no_ceiling(self):
        """max_pull=None walks until the server runs out, not to a fixed cap."""
        for _ in range(25):
            responses.add(responses.GET, PROTOCOLS_URL,
                          json={"items": [make_protocol(i) for i in range(10)]})
        responses.add(responses.GET, PROTOCOLS_URL,
                      json={"items": [make_protocol(999)]})  # short page -> stop

        results = get_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10)

        assert len(responses.calls) == 26   # past the old 20-page ceiling
        assert len(results) == 251

    @responses.activate
    def test_explicit_cap_still_applies(self):
        """None is the default, not the only option."""
        page = [make_protocol(i) for i in range(10)]
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": page})

        results = get_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10, max_pull=2)
        assert len(responses.calls) == 2
        assert len(results) == 20


# -----------------------------------------------------------------------------#
# 2b. THE SERVER'S OWN PAGINATION BLOCK
# -----------------------------------------------------------------------------#
def paged(items, next_page, total):
    """A protocols.io-shaped response envelope."""
    return {
        "items": items,
        "pagination": {"next_page": next_page, "total_results": total,
                       "page_size": 10},
        "status_code": 0,
    }


class TestEnvelopeDrivenPagination:
    @responses.activate
    def test_walk_starts_at_page_zero(self):
        """page_id is 0-indexed upstream — starting at 1 skips ten protocols."""
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged([make_protocol(1)], None, 1))

        get_protocol_list(PROTOCOLS_URL, HEADERS)

        assert "page_id=0" in responses.calls[0].request.url

    @responses.activate
    def test_follows_next_page_not_page_length(self):
        """A full page with next_page=None ends the walk immediately."""
        full = [make_protocol(i) for i in range(10)]
        responses.add(responses.GET, PROTOCOLS_URL, json=paged(full, None, 10))

        result = get_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10)

        assert len(responses.calls) == 1   # would have fetched again on length alone
        assert len(result) == 10

    @responses.activate
    def test_continues_on_short_page_when_next_page_is_set(self):
        """Conversely, a short page is NOT the end if the server says otherwise."""
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged([make_protocol(1)], "?page_id=1", 2))
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged([make_protocol(2)], None, 2))

        result = get_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10)

        assert len(responses.calls) == 2
        assert len(result) == 2

    @responses.activate
    def test_count_mismatch_retries_once_then_succeeds(self):
        """A transient short pull is retried whole, and the retry is trusted."""
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged([make_protocol(1)], None, 2))     # 1 of 2 -> retry
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged([make_protocol(1), make_protocol(2)], None, 2))

        result = get_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10)

        assert len(responses.calls) == 2
        assert len(result) == 2

    @responses.activate
    def test_persistent_mismatch_raises(self):
        """Refuse to hand back a pull the server says is incomplete."""
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged([make_protocol(1)], None, 61))

        with pytest.raises(IncompletePullError, match="61"):
            get_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10)

        assert len(responses.calls) == 2   # original + exactly one retry

    @responses.activate
    def test_capped_walk_is_not_verified(self):
        """An intentional cap is partial by design — must not raise."""
        full = [make_protocol(i) for i in range(10)]
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged(full, "?page_id=1", 999))

        result = get_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10, max_pull=1)
        assert len(result) == 10

    @responses.activate
    def test_resumed_walk_is_not_verified(self):
        """Starting mid-way is partial by design too."""
        responses.add(responses.GET, PROTOCOLS_URL,
                      json=paged([make_protocol(1)], None, 999))

        result = get_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10, page_id=5)
        assert len(result) == 1

    @responses.activate
    def test_endpoint_without_pagination_still_works(self):
        """Progressive enhancement: no pagination block -> short-page fallback."""
        responses.add(responses.GET, PROTOCOLS_URL,
                      json={"items": [make_protocol(i) for i in range(10)]})
        responses.add(responses.GET, PROTOCOLS_URL,
                      json={"items": [make_protocol(11)]})

        result = get_protocol_list(PROTOCOLS_URL, HEADERS, page_size=10)
        assert len(result) == 11




# -----------------------------------------------------------------------------#
# 3. PULLING THE SAME FIELDS  (allowlist selection + hash stability)
# -----------------------------------------------------------------------------#
class TestFieldSelection:
    def test_source_contract_matches_spec(self):
        """The implementation's allowlist is exactly the spec above.

        A guard, not a tautology: STABLE_FIELDS defines protocol identity, so an
        unreviewed edit to it silently re-hashes every version in the store.
        Changing the contract must require changing this test too.
        """
        assert list(SOURCE_STABLE_FIELDS) == STABLE_FIELDS
        assert list(SOURCE_METADATA_FIELDS) == METADATA_FIELDS

    def test_only_allowed_fields_survive(self):
        """Selection keeps the hashed fields plus metadata — no more, no less."""
        selected = select_protocol(make_protocol(1))
        assert sorted(selected) == sorted(STABLE_FIELDS + METADATA_FIELDS)

    def test_metadata_is_not_hashed(self):
        """created_on is stored but must not participate in identity."""
        selected = select_protocol(make_protocol(1))
        assert "created_on" in selected
        assert "created_on" not in hashable_content(selected)

    def test_created_on_change_does_not_change_hash(self):
        """Re-authoring dates move; the protocol text has not changed."""
        h1 = protocol_hash(select_protocol(make_protocol(1, created_on=1745934254)))
        h2 = protocol_hash(select_protocol(make_protocol(1, created_on=1600000000)))
        assert h1 == h2

    def test_missing_metadata_is_tolerated(self):
        """Metadata is optional — absence must not abort a pull."""
        p = make_protocol(1)
        del p["created_on"]
        selected = select_protocol(p)
        assert "created_on" not in selected
        complete = select_protocol(make_protocol(1))
        assert protocol_hash(selected) == protocol_hash(complete)

    def test_unknown_upstream_field_is_ignored(self):
        """The allowlist payoff: a NEW field from the API cannot enter the hash.

        Under a denylist, the day protocols.io adds a field we have not thought
        to exclude, every protocol gets a phantom new version on the next pull.
        """
        p1 = make_protocol(1)
        p2 = make_protocol(1, brand_new_api_field={"token": "CHANGES-EVERY-CALL"})

        assert "brand_new_api_field" not in select_protocol(p2)
        assert protocol_hash(select_protocol(p1)) == protocol_hash(select_protocol(p2))

    def test_missing_stable_field_raises(self):
        """An absent contract field is an upstream schema change — fail loudly.

        Defaulting it would hash as "field removed" and mint a bogus version for
        every affected protocol.
        """
        incomplete = make_protocol(1)
        del incomplete["materials_text"]

        with pytest.raises(MissingStableFieldsError, match="materials_text"):
            select_protocol(incomplete)

    def test_custom_include_fields_narrow_the_contract(self):
        """Both field sets are overridable — the defaults are defaults, not laws."""
        selected = select_protocol(make_protocol(1), ["id", "title"], [])
        assert sorted(selected) == ["id", "title"]

    def test_metadata_defaults_still_apply_when_narrowing(self):
        """Narrowing the hashed set does not silently drop metadata."""
        selected = select_protocol(make_protocol(1), ["id", "title"])
        assert sorted(selected) == sorted(METADATA_FIELDS + ["id", "title"])

    def test_hash_is_deterministic(self):
        """Same content -> same hash (canonical serialization)."""
        a = protocol_hash(select_protocol(make_protocol(1)))
        b = protocol_hash(select_protocol(make_protocol(1)))
        assert a == b

    def test_hash_ignores_signed_url_noise(self):
        """The whole point: image/versions/stats noise must NOT change the hash."""
        p1 = make_protocol(1)
        p2 = make_protocol(1)
        p2["image"] = {"placeholder": "https://files.x/y.jpg?Policy=DIFFERENT"}
        p2["versions"] = [{"id": 1, "image": {"source": "?Policy=DIFFERENT"}}]
        p2["stats"] = {"number_of_views": 999999}

        h1 = protocol_hash(select_protocol(p1))
        h2 = protocol_hash(select_protocol(p2))
        assert h1 == h2

    def test_real_content_change_changes_hash(self):
        """A genuine change to semantic content DOES produce a new hash."""
        h1 = protocol_hash(select_protocol(make_protocol(1, title="Original")))
        h2 = protocol_hash(select_protocol(make_protocol(1, title="Edited")))
        assert h1 != h2

    def test_process_protocols_dedupes_by_hash(self):
        """Identical duplicates collapse; distinct protocols stay separate."""
        protocols = [make_protocol(1), make_protocol(1), make_protocol(2)]
        processed = process_protocols(protocols)
        assert len(processed) == 2  # id-1 dup collapsed, id-2 distinct

    def test_process_protocols_propagates_missing_field(self):
        """A bad record fails the whole pull rather than being quietly stored."""
        incomplete = make_protocol(2)
        del incomplete["units"]

        with pytest.raises(MissingStableFieldsError, match="units"):
            process_protocols([make_protocol(1), incomplete])


# -----------------------------------------------------------------------------#
# 4. A DATABASE BEING BUILT
# -----------------------------------------------------------------------------#
class TestDatabaseBuild:
    EXPECTED_COLUMNS = [
        "hash", "protocol_id", "protocol_name", "protocol_guid",
        "protocol", "created_on", "authors", "creator",
        "valid_from", "deprecated_at",
    ]

    def test_table_is_created(self, db_path):
        initialize_db(db_path)
        conn = sqlite3.connect(db_path)
        table = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='protocol_versions'"
        ).fetchone()
        assert table is not None

    def test_schema_has_expected_columns(self, db_path):
        initialize_db(db_path)
        conn = sqlite3.connect(db_path)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(protocol_versions)")]
        assert columns == self.EXPECTED_COLUMNS

    def test_initialize_is_idempotent(self, db_path):
        """Calling twice is a harmless no-op (safe to run on every sync)."""
        initialize_db(db_path)
        initialize_db(db_path)  # must not raise
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM protocol_versions").fetchone()[0]
        assert count == 0


# -----------------------------------------------------------------------------#
# 5. DATA INSERTED CORRECTLY
# -----------------------------------------------------------------------------#
class TestDataInsertion:
    def test_rows_are_inserted(self, db_path):
        processed = process_protocols([make_protocol(1), make_protocol(2)])
        rows = to_rows(processed)

        initialize_db(db_path)
        n_new = insert_protocols(db_path, rows)

        assert n_new == 2
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM protocol_versions").fetchone()[0]
        assert count == 2

    def test_inserted_values_match_source(self, db_path):
        processed = process_protocols([make_protocol(1, title="My Protocol")])
        initialize_db(db_path)
        insert_protocols(db_path, to_rows(processed))

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT protocol_id, protocol_name, protocol_guid, "
            "created_on, valid_from, deprecated_at FROM protocol_versions"
        ).fetchone()
        assert row[0] == "1"
        assert row[1] == "My Protocol"
        assert row[2] == f"{1:032X}"
        assert row[3] == 1745934254      # raw epoch retained
        assert row[4] == 1745934254      # valid_from backdated from created_on
        assert as_date(row[4]) == "2025-04-29"
        assert row[5] is None            # deprecated_at unset for a live version


# -----------------------------------------------------------------------------#
# 5b. VALID_FROM / BACKDATING
# -----------------------------------------------------------------------------#
PULLED_AT = to_epoch("2026-07-27")


class TestValidFrom:
    def test_backdates_to_created_on(self):
        """A protocol authored before this store existed opens at creation."""
        row = to_row(select_protocol(make_protocol(1)), pulled_at=PULLED_AT)
        assert row[8] == 1745934254
        assert as_date(row[8]) == "2025-04-29"

    def test_falls_back_to_pull_time(self):
        """No created_on -> the interval opens when we first saw it."""
        p = make_protocol(1)
        del p["created_on"]
        row = to_row(select_protocol(p), pulled_at=PULLED_AT)
        assert row[5] is None          # nothing to store
        assert row[8] == PULLED_AT     # pull time instead

    def test_falsy_created_on_falls_back(self):
        """created_on of 0 is not a real epoch — treat it as absent."""
        row = to_row(select_protocol(make_protocol(1, created_on=0)),
                     pulled_at=PULLED_AT)
        assert row[8] == PULLED_AT

    def test_one_pull_shares_one_timestamp(self):
        """Every row in a batch gets the same fallback time, not per-row clocks."""
        a, b = make_protocol(1), make_protocol(2)
        del a["created_on"]
        del b["created_on"]
        rows = to_rows(process_protocols([a, b]), pulled_at=PULLED_AT)
        assert {r[8] for r in rows} == {PULLED_AT}

    def test_dates_are_stored_as_integers(self):
        """The store holds epoch seconds only — no date strings."""
        row = to_row(select_protocol(make_protocol(1)), pulled_at=PULLED_AT)
        assert isinstance(row[5], int)   # created_on
        assert isinstance(row[8], int)   # valid_from
        assert row[9] is None            # deprecated_at

    def test_created_on_does_not_affect_stored_blob(self):
        """The blob holds identity only, so it still rehashes to its key."""
        row = to_row(select_protocol(make_protocol(1)))
        assert "created_on" not in json.loads(row[4])
        assert content_hash(json.loads(row[4])) == row[0]


# -----------------------------------------------------------------------------#
# 5c. METADATA COLUMNS
# -----------------------------------------------------------------------------#
class TestMetadataColumns:
    def test_authors_and_creator_are_stored(self, db_path):
        initialize_db(db_path)
        insert_protocols(db_path, to_rows(process_protocols([make_protocol(1)])))

        conn = sqlite3.connect(db_path)
        authors, creator = conn.execute(
            "SELECT authors, creator FROM protocol_versions"
        ).fetchone()
        assert json.loads(authors)[0]["name"] == "A. Researcher"
        assert json.loads(creator)["username"] == "aresearcher"

    def test_attribution_change_is_not_a_version_change(self, db_path):
        """Re-attribution is metadata, not content — no new hash."""
        initialize_db(db_path)
        insert_protocols(db_path, to_rows(process_protocols([make_protocol(1)])))

        reattributed = make_protocol(1, creator={"name": "B. Other",
                                                 "username": "bother"})
        rows = to_rows(process_protocols([reattributed]))
        assert diff_pull(db_path, rows)["unchanged"] == ["1"]

    def test_missing_metadata_is_null_not_an_error(self, db_path):
        p = make_protocol(1)
        del p["authors"]
        del p["creator"]
        initialize_db(db_path)
        insert_protocols(db_path, to_rows(process_protocols([p])))

        conn = sqlite3.connect(db_path)
        assert conn.execute(
            "SELECT authors, creator FROM protocol_versions"
        ).fetchone() == (None, None)

    def test_guidelines_change_is_a_version_change(self, db_path):
        """guidelines is protocol content — editing it must fork the version."""
        initialize_db(db_path)
        insert_protocols(db_path, to_rows(process_protocols([make_protocol(1)])))

        edited = make_protocol(1, guidelines='{"blocks":[{"key":"a1",'
                                             '"text":"Wear TWO gloves."}]}')
        rows = to_rows(process_protocols([edited]))
        assert diff_pull(db_path, rows)["changed"] == ["1"]

    def test_insert_is_idempotent(self, db_path):
        """Re-inserting the same content is a no-op (content-hash primary key)."""
        rows = to_rows(process_protocols([make_protocol(1), make_protocol(2)]))
        initialize_db(db_path)
        # Is this enough to make sure that new elements are added and other discarded
        assert insert_protocols(db_path, rows) == 2
        assert insert_protocols(db_path, rows) == 0  # nothing new the second time


# -----------------------------------------------------------------------------#
# 6. STORED BLOB AND ITS HASH AGREE
# -----------------------------------------------------------------------------#
class TestStoredHashIntegrity:
    def test_row_hash_is_the_hash_of_its_own_blob(self):
        row = to_row(select_protocol(make_protocol(1)))
        stored_hash, blob = row[0], row[4]
        assert content_hash(json.loads(blob)) == stored_hash

    def test_row_hash_matches_the_pull_hash(self):
        """db.py and request_utils.py must not drift into two hashing schemes."""
        selected = select_protocol(make_protocol(1))
        assert to_row(selected)[0] == protocol_hash(selected)

    def test_stored_blob_rehashes_to_its_key(self, db_path):
        initialize_db(db_path)
        insert_protocols(db_path, to_rows(process_protocols(
            [make_protocol(1), make_protocol(2)]
        )))

        conn = sqlite3.connect(db_path)
        for stored_hash, blob in conn.execute(
            "SELECT hash, protocol FROM protocol_versions"
        ):
            assert content_hash(json.loads(blob)) == stored_hash

    def test_verify_passes_on_untampered_db(self, db_path):
        initialize_db(db_path)
        insert_protocols(db_path, to_rows(process_protocols([make_protocol(1)])))
        assert verify_protocols(db_path) == []

    def test_verify_catches_tampering(self, db_path):
        """Editing a stored blob out from under its hash must be detectable."""
        initialize_db(db_path)
        insert_protocols(db_path, to_rows(process_protocols([make_protocol(1)])))

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE protocol_versions SET protocol = ?",
                ('{"id":1,"title":"TAMPERED"}',),
            )

        assert len(verify_protocols(db_path)) == 1

    def test_unicode_title_round_trips(self, db_path):
        """NFD input is normalized once, so the stored blob still verifies."""
        nfd = unicodedata.normalize("NFD", "Protocole café")
        initialize_db(db_path)
        insert_protocols(db_path, to_rows(process_protocols(
            [make_protocol(1, title=nfd)]
        )))
        assert verify_protocols(db_path) == []


# -----------------------------------------------------------------------------#
# 7. DETECTING WHAT CHANGED
# -----------------------------------------------------------------------------#
class TestChangeDetection:
    def test_empty_db_sees_everything_as_new(self, db_path):
        initialize_db(db_path)
        rows = to_rows(process_protocols([make_protocol(1), make_protocol(2)]))

        diff = diff_pull(db_path, rows)
        assert diff["new"] == ["1", "2"]
        assert diff["changed"] == []
        assert diff["unchanged"] == []
        assert diff["absent"] == []

    def test_identical_pull_is_all_unchanged(self, db_path):
        initialize_db(db_path)
        rows = to_rows(process_protocols([make_protocol(1), make_protocol(2)]))
        insert_protocols(db_path, rows)

        diff = diff_pull(db_path, rows)
        assert diff["unchanged"] == ["1", "2"]
        assert diff["new"] == []
        assert diff["changed"] == []

    def test_edited_protocol_is_changed_not_new(self, db_path):
        """Same protocol_id, different content hash -> changed."""
        initialize_db(db_path)
        insert_protocols(db_path, to_rows(process_protocols(
            [make_protocol(1, title="Original")]
        )))

        rows = to_rows(process_protocols([make_protocol(1, title="Edited")]))
        diff = diff_pull(db_path, rows)
        assert diff["changed"] == ["1"]
        assert diff["new"] == []

    def test_signed_url_noise_is_not_a_change(self, db_path):
        """The point of the allowlist: request-time noise must not read as an edit."""
        initialize_db(db_path)
        insert_protocols(db_path, to_rows(process_protocols([make_protocol(1)])))

        noisy = make_protocol(1)
        noisy["image"] = {"placeholder": "https://files.x/y.jpg?Policy=NEW-TOKEN"}
        noisy["stats"] = {"number_of_views": 999999}

        diff = diff_pull(db_path, to_rows(process_protocols([noisy])))
        assert diff["unchanged"] == ["1"]
        assert diff["changed"] == []

    def test_dropped_protocol_is_absent(self, db_path):
        """Content-addressing cannot see absence — the id-set diff has to."""
        initialize_db(db_path)
        insert_protocols(db_path, to_rows(process_protocols(
            [make_protocol(1), make_protocol(2)]
        )))

        diff = diff_pull(db_path, to_rows(process_protocols([make_protocol(1)])))
        assert diff["absent"] == ["2"]
        assert diff["unchanged"] == ["1"]

    def test_active_hashes_ignores_deprecated(self, db_path):
        initialize_db(db_path)
        insert_protocols(db_path, to_rows(process_protocols([make_protocol(1)])))
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE protocol_versions SET deprecated_at = '2025-01-01'")

        assert get_active_hashes(db_path) == {}

