# -----------------------------------------------------------------------------#
# TESTS — protocols.io pull → strip/hash → sqlite pipeline
# -----------------------------------------------------------------------------#
import json
import sqlite3
import unicodedata

import pytest
import responses

from chronos.utils.request_utils import (
    IncompletePullError,
    get_protocol_list,
    process_protocols,
)

from seal.contract import METADATA_FIELDS as SOURCE_METADATA_FIELDS
from seal.contract import STABLE_FIELDS as SOURCE_STABLE_FIELDS
from seal.contract import (
    MissingStableFieldsError,
    hashable_content,
    protocol_hash,
    select_protocol,
    content_hash
)
from seal.dates import as_date, to_epoch
from seal.store import (
    DuplicateProtocolIdError,
    connect,
    diff_pull,
    get_active_hashes,
    initialize_db,
    build_row,
    format_entry,
    verify_protocols,
    write_pull,
)

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
def columns_of(db: str, table: str) -> list[str]:
    with connect(db, read_only=True) as conn:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def query(db: str, sql: str, *params):
    with connect(db, read_only=True) as conn:
        return conn.execute(sql, params).fetchall()


class TestDatabaseBuild:
    CONTENT_COLUMNS = [
        "hash", "protocol_id", "protocol_guid", "protocol_name",
        "protocol", "created_on", "authors", "creator",
    ]
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
        initialize_db(db_path)  # must not raise
        assert query(db_path, "SELECT COUNT(*) FROM protocol_content") == [(0,)]


# -----------------------------------------------------------------------------#
# 4b. CONNECTION LIFETIME
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

    def test_every_connection_is_closed(self, db_path, monkeypatch):
        """`with sqlite3.connect(...)` commits but does NOT close — ours must."""
        opened = self.spy_on_connections(monkeypatch)

        initialize_db(db_path)
        rows = format_entry(process_protocols([make_protocol(1)]))
        write_pull(db_path, rows)
        diff_pull(db_path, rows)
        get_active_hashes(db_path)
        verify_protocols(db_path)

        assert len(opened) == 5
        for conn in opened:
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_connection_is_closed_even_when_the_body_raises(self, db_path,
                                                            monkeypatch):
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


# -----------------------------------------------------------------------------#
# 5. DATA INSERTED CORRECTLY
# -----------------------------------------------------------------------------#
class TestDataInsertion:
    def test_rows_are_inserted(self, db_path):
        processed = process_protocols([make_protocol(1), make_protocol(2)])
        rows = format_entry(processed)

        initialize_db(db_path)
        diff = write_pull(db_path, rows)

        assert diff["new"] == ["1", "2"]
        assert query(db_path, "SELECT COUNT(*) FROM protocol_content") == [(2,)]
        assert query(db_path, "SELECT COUNT(*) FROM protocol_history") == [(2,)]

    def test_inserted_values_match_source(self, db_path):
        processed = process_protocols([make_protocol(1, title="My Protocol")])
        initialize_db(db_path)
        write_pull(db_path, format_entry(processed))

        content = query(
            db_path,
            "SELECT protocol_id, protocol_guid, protocol_name, created_on "
            "FROM protocol_content",
        )[0]
        assert content[0] == "1"
        assert content[1] == f"{1:032X}"
        assert content[2] == "My Protocol"
        assert content[3] == 1745934254      # raw epoch retained

        history = query(
            db_path,
            "SELECT protocol_id, valid_from, deprecated_at FROM protocol_history",
        )[0]
        assert history[0] == "1"
        assert history[1] == 1745934254      # valid_from backdated from created_on
        assert as_date(history[1]) == "2025-04-29"
        assert history[2] is None            # live version

    def test_history_hash_points_at_stored_content(self, db_path):
        """The FK is real: history can only reference content that exists."""
        initialize_db(db_path)
        with pytest.raises(sqlite3.IntegrityError):
            with connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO protocol_history "
                    "(protocol_id, hash, valid_from) VALUES ('1', 'nope', 1)"
                )


# -----------------------------------------------------------------------------#
# 5b. VALID_FROM / BACKDATING
# -----------------------------------------------------------------------------#
PULLED_AT = to_epoch("2026-07-27")
LATER = to_epoch("2026-08-03")


class TestValidFrom:
    def test_backdates_to_created_on(self):
        """A protocol authored before this store existed opens at creation."""
        row = build_row(select_protocol(make_protocol(1)), pulled_at=PULLED_AT)
        assert row.valid_from == 1745934254
        assert as_date(row.valid_from) == "2025-04-29"

    def test_falls_back_to_pull_time(self):
        """No created_on -> the interval opens when we first saw it."""
        p = make_protocol(1)
        del p["created_on"]
        row = build_row(select_protocol(p), pulled_at=PULLED_AT)
        assert row.created_on is None      # nothing to store
        assert row.valid_from == PULLED_AT  # pull time instead

    def test_falsy_created_on_falls_back(self):
        """created_on of 0 is not a real epoch — treat it as absent."""
        row = build_row(select_protocol(make_protocol(1, created_on=0)),
                     pulled_at=PULLED_AT)
        assert row.valid_from == PULLED_AT

    def test_one_pull_shares_one_timestamp(self):
        """Every row in a batch gets the same fallback time, not per-row clocks."""
        a, b = make_protocol(1), make_protocol(2)
        del a["created_on"]
        del b["created_on"]
        rows = format_entry(process_protocols([a, b]), pulled_at=PULLED_AT)
        assert {r.valid_from for r in rows} == {PULLED_AT}

    def test_dates_are_stored_as_integers(self):
        """The store holds epoch seconds only — no date strings."""
        row = build_row(select_protocol(make_protocol(1)), pulled_at=PULLED_AT)
        assert isinstance(row.created_on, int)
        assert isinstance(row.valid_from, int)

    def test_created_on_does_not_affect_stored_blob(self):
        """The blob holds identity only, so it still rehashes to its key."""
        row = build_row(select_protocol(make_protocol(1)))
        assert "created_on" not in json.loads(row.blob)
        assert content_hash(json.loads(row.blob)) == row.hash

    def test_only_the_first_version_backdates(self, db_path):
        """A later version opens at the pull, not at the protocol's birthday.

        created_on describes when the PROTOCOL was authored, not when this
        version was made. Backdating a second version to it would reopen inside
        the first version's interval and leave two versions active at once.
        """
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1, title="Original")]
        ), PULLED_AT), PULLED_AT)
        write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1, title="Edited")]
        ), LATER), LATER)

        intervals = query(
            db_path,
            "SELECT valid_from, deprecated_at FROM protocol_history "
            "ORDER BY valid_from",
        )
        assert intervals == [(1745934254, LATER), (LATER, None)]


# -----------------------------------------------------------------------------#
# 5c. METADATA COLUMNS
# -----------------------------------------------------------------------------#
class TestMetadataColumns:
    def test_authors_and_creator_are_stored(self, db_path):
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols([make_protocol(1)])))

        authors, creator = query(
            db_path, "SELECT authors, creator FROM protocol_content"
        )[0]
        assert json.loads(authors)[0]["name"] == "A. Researcher"
        assert json.loads(creator)["username"] == "aresearcher"

    def test_attribution_change_is_not_a_version_change(self, db_path):
        """Re-attribution is metadata, not content — no new hash."""
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols([make_protocol(1)])))

        reattributed = make_protocol(1, creator={"name": "B. Other",
                                                 "username": "bother"})
        rows = format_entry(process_protocols([reattributed]))
        assert diff_pull(db_path, rows)["unchanged"] == ["1"]

    def test_missing_metadata_is_null_not_an_error(self, db_path):
        p = make_protocol(1)
        del p["authors"]
        del p["creator"]
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols([p])))

        assert query(
            db_path, "SELECT authors, creator FROM protocol_content"
        ) == [(None, None)]

    def test_guidelines_change_is_a_version_change(self, db_path):
        """guidelines is protocol content — editing it must fork the version."""
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols([make_protocol(1)])))

        edited = make_protocol(1, guidelines='{"blocks":[{"key":"a1",'
                                             '"text":"Wear TWO gloves."}]}')
        rows = format_entry(process_protocols([edited]))
        assert diff_pull(db_path, rows)["changed"] == ["1"]

    def test_write_is_idempotent(self, db_path):
        """Re-pulling identical content is a no-op (content-hash primary key)."""
        rows = format_entry(process_protocols([make_protocol(1), make_protocol(2)]))
        initialize_db(db_path)
        assert write_pull(db_path, rows)["new"] == ["1", "2"]

        diff = write_pull(db_path, rows)
        assert diff["unchanged"] == ["1", "2"]
        assert diff["new"] == []
        assert query(db_path, "SELECT COUNT(*) FROM protocol_history") == [(2,)]


# -----------------------------------------------------------------------------#
# 6. STORED BLOB AND ITS HASH AGREE
# -----------------------------------------------------------------------------#
class TestStoredHashIntegrity:
    def test_row_hash_is_the_hash_of_its_own_blob(self):
        row = build_row(select_protocol(make_protocol(1)))
        assert content_hash(json.loads(row.blob)) == row.hash

    def test_row_hash_matches_the_pull_hash(self):
        """store.py and request_utils.py must not drift into two hash schemes."""
        selected = select_protocol(make_protocol(1))
        assert build_row(selected).hash == protocol_hash(selected)

    def test_stored_blob_rehashes_to_its_key(self, db_path):
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1), make_protocol(2)]
        )))

        for stored_hash, blob in query(
            db_path, "SELECT hash, protocol FROM protocol_content"
        ):
            assert content_hash(json.loads(blob)) == stored_hash

    def test_verify_passes_on_untampered_db(self, db_path):
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols([make_protocol(1)])))
        assert verify_protocols(db_path) == []

    def test_verify_catches_tampering(self, db_path):
        """Editing a stored blob out from under its hash must be detectable."""
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols([make_protocol(1)])))

        with connect(db_path) as conn:
            conn.execute(
                "UPDATE protocol_content SET protocol = ?",
                ('{"id":1,"title":"TAMPERED"}',),
            )

        assert len(verify_protocols(db_path)) == 1

    def test_unicode_title_round_trips(self, db_path):
        """NFD input is normalized once, so the stored blob still verifies."""
        nfd = unicodedata.normalize("NFD", "Protocole café")
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1, title=nfd)]
        )))
        assert verify_protocols(db_path) == []


# -----------------------------------------------------------------------------#
# 7. DETECTING WHAT CHANGED
# -----------------------------------------------------------------------------#
class TestChangeDetection:
    def test_empty_db_sees_everything_as_new(self, db_path):
        initialize_db(db_path)
        rows = format_entry(process_protocols([make_protocol(1), make_protocol(2)]))

        diff = diff_pull(db_path, rows)
        assert diff["new"] == ["1", "2"]
        assert diff["changed"] == []
        assert diff["unchanged"] == []
        assert diff["absent"] == []

    def test_identical_pull_is_all_unchanged(self, db_path):
        initialize_db(db_path)
        rows = format_entry(process_protocols([make_protocol(1), make_protocol(2)]))
        write_pull(db_path, rows)

        diff = diff_pull(db_path, rows)
        assert diff["unchanged"] == ["1", "2"]
        assert diff["new"] == []
        assert diff["changed"] == []

    def test_edited_protocol_is_changed_not_new(self, db_path):
        """Same protocol_id, different content hash -> changed."""
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1, title="Original")]
        )))

        rows = format_entry(process_protocols([make_protocol(1, title="Edited")]))
        diff = diff_pull(db_path, rows)
        assert diff["changed"] == ["1"]
        assert diff["new"] == []

    def test_signed_url_noise_is_not_a_change(self, db_path):
        """The point of the allowlist: request-time noise must not read as an edit."""
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols([make_protocol(1)])))

        noisy = make_protocol(1)
        noisy["image"] = {"placeholder": "https://files.x/y.jpg?Policy=NEW-TOKEN"}
        noisy["stats"] = {"number_of_views": 999999}

        diff = diff_pull(db_path, format_entry(process_protocols([noisy])))
        assert diff["unchanged"] == ["1"]
        assert diff["changed"] == []

    def test_dropped_protocol_is_absent(self, db_path):
        """Content-addressing cannot see absence — the id-set diff has to."""
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1), make_protocol(2)]
        )))

        diff = diff_pull(db_path, format_entry(process_protocols([make_protocol(1)])))
        assert diff["absent"] == ["2"]
        assert diff["unchanged"] == ["1"]

    def test_active_hashes_ignores_deprecated(self, db_path):
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols([make_protocol(1)])))
        with connect(db_path) as conn:
            conn.execute("UPDATE protocol_history SET deprecated_at = 1")

        assert get_active_hashes(db_path) == {}


# -----------------------------------------------------------------------------#
# 8. THE WRITE PATH — INTERVALS MOVING
# -----------------------------------------------------------------------------#
FOREVER = 9223372036854775807


def active_count(db: str) -> list[tuple]:
    """protocol_ids with more than one live version — must always be empty."""
    return query(
        db,
        "SELECT protocol_id, COUNT(*) FROM protocol_history "
        "WHERE deprecated_at IS NULL GROUP BY protocol_id HAVING COUNT(*) > 1",
    )


def overlaps(db: str) -> list[tuple]:
    """protocol_ids whose validity intervals overlap at ANY instant.

    Stronger than active_count, which only sees rows that are open right now:
    a reopened interval can sit inside a closed one and still make two versions
    resolve as active for a past date T.
    """
    return query(
        db,
        "SELECT a.protocol_id, a.valid_from, b.valid_from "
        "FROM protocol_history a JOIN protocol_history b "
        "  ON a.protocol_id = b.protocol_id AND a.rowid < b.rowid "
        f"WHERE a.valid_from < COALESCE(b.deprecated_at, {FOREVER}) "
        f"  AND b.valid_from < COALESCE(a.deprecated_at, {FOREVER})",
    )


class TestWritePath:
    def test_deprecate_on_change(self, db_path):
        """A new hash closes the prior interval and opens a new one."""
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1, title="Original")]
        ), PULLED_AT), PULLED_AT)
        write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1, title="Edited")]
        ), LATER), LATER)

        history = query(
            db_path,
            "SELECT protocol_name, valid_from, deprecated_at "
            "FROM protocol_history JOIN protocol_content USING (hash) "
            "ORDER BY valid_from",
        )
        assert [h[0] for h in history] == ["Original", "Edited"]
        assert history[0][2] == LATER   # old interval closed at the pull
        assert history[1][2] is None    # new interval open

    def test_deprecate_on_absence(self, db_path):
        """A protocol that vanishes upstream is closed by the id-set diff."""
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1), make_protocol(2)]
        ), PULLED_AT), PULLED_AT)

        diff = write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1)]
        ), LATER), LATER)

        assert diff["absent"] == ["2"]
        assert get_active_hashes(db_path).keys() == {"1"}
        assert query(
            db_path,
            "SELECT deprecated_at FROM protocol_history WHERE protocol_id = '2'",
        ) == [(LATER,)]

    def test_blob_survives_deprecation(self, db_path):
        """Old content stays resolvable by hash forever, so pins reproduce."""
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1), make_protocol(2)]
        ), PULLED_AT), PULLED_AT)
        write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1, title="Edited")]
        ), LATER), LATER)

        # id-2 deprecated by absence, id-1's first version by change:
        # three distinct blobs, none deleted.
        assert query(db_path, "SELECT COUNT(*) FROM protocol_content") == [(3,)]
        assert verify_protocols(db_path) == []

    def test_at_most_one_active_version_per_id(self, db_path):
        """The invariant that makes a named protocol resolve unambiguously."""
        initialize_db(db_path)
        for title, stamp in [("v1", PULLED_AT), ("v2", LATER), ("v3", LATER + 1)]:
            write_pull(db_path, format_entry(process_protocols(
                [make_protocol(1, title=title), make_protocol(2)]
            ), stamp), stamp)

        assert active_count(db_path) == []
        assert overlaps(db_path) == []
        assert len(get_active_hashes(db_path)) == 2

    def test_intervals_do_not_overlap(self, db_path):
        """Each interval starts exactly where the previous one closed."""
        initialize_db(db_path)
        for title, stamp in [("v1", PULLED_AT), ("v2", LATER), ("v3", LATER + 1)]:
            write_pull(db_path, format_entry(process_protocols(
                [make_protocol(1, title=title)]
            ), stamp), stamp)

        intervals = query(
            db_path,
            "SELECT valid_from, deprecated_at FROM protocol_history "
            "WHERE protocol_id = '1' ORDER BY valid_from",
        )
        assert intervals == [
            (1745934254, LATER), (LATER, LATER + 1), (LATER + 1, None)
        ]

    def test_unchanged_protocol_keeps_its_original_interval(self, db_path):
        """A no-op pull must not churn history."""
        initialize_db(db_path)
        rows = format_entry(process_protocols([make_protocol(1)]), PULLED_AT)
        write_pull(db_path, rows, PULLED_AT)
        write_pull(db_path, format_entry(process_protocols([make_protocol(1)]),
                                    LATER), LATER)

        assert query(
            db_path, "SELECT valid_from, deprecated_at FROM protocol_history"
        ) == [(1745934254, None)]

    def test_revert_to_previous_content_reopens_an_interval(self, db_path):
        """Undoing an edit reuses the stored blob but is a new interval."""
        initialize_db(db_path)
        original = format_entry(process_protocols([make_protocol(1, title="A")]),
                           PULLED_AT)
        write_pull(db_path, original, PULLED_AT)
        write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1, title="B")]
        ), LATER), LATER)
        diff = write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1, title="A")]
        ), LATER + 1), LATER + 1)

        assert diff["changed"] == ["1"]
        # Two blobs, three intervals: content deduped, history not.
        assert query(db_path, "SELECT COUNT(*) FROM protocol_content") == [(2,)]
        assert query(db_path, "SELECT COUNT(*) FROM protocol_history") == [(3,)]
        assert get_active_hashes(db_path)["1"] == original[0].hash

    def test_reappearing_protocol_opens_a_new_interval(self, db_path):
        """Absent then back: a new interval, NOT a backdate into the closed one.

        diff calls it "new" because nothing is active for that id, but it has
        history — backdating to created_on here would reopen at 2025-04-29 and
        two versions would resolve as active for every date in the gap.
        """
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1), make_protocol(2)]
        ), PULLED_AT), PULLED_AT)
        write_pull(db_path, format_entry(process_protocols([make_protocol(1)]),
                                    LATER), LATER)
        diff = write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1), make_protocol(2)]
        ), LATER + 1), LATER + 1)

        assert diff["new"] == ["2"]
        assert query(
            db_path,
            "SELECT valid_from, deprecated_at FROM protocol_history "
            "WHERE protocol_id = '2' ORDER BY valid_from",
        ) == [(1745934254, LATER), (LATER + 1, None)]
        assert active_count(db_path) == []
        assert overlaps(db_path) == []

    def test_no_interval_overlaps_across_a_churny_history(self, db_path):
        """Edits, disappearances and returns interleaved — still unambiguous."""
        initialize_db(db_path)
        pulls = [
            ([make_protocol(1, title="A"), make_protocol(2)], PULLED_AT),
            ([make_protocol(1, title="B")], LATER),
            ([make_protocol(1, title="B"), make_protocol(2)], LATER + 1),
            ([make_protocol(2, title="C")], LATER + 2),
            ([make_protocol(1, title="A"), make_protocol(2, title="C")],
             LATER + 3),
        ]
        for protocols, stamp in pulls:
            write_pull(db_path, format_entry(process_protocols(protocols), stamp),
                       stamp)

        assert overlaps(db_path) == []
        assert active_count(db_path) == []
        assert verify_protocols(db_path) == []

    def test_duplicate_protocol_id_in_one_pull_raises(self, db_path):
        """Two versions of one protocol in a single pull would break the invariant."""
        initialize_db(db_path)
        rows = format_entry(process_protocols(
            [make_protocol(1, title="A"), make_protocol(1, title="B")]
        ), PULLED_AT)

        with pytest.raises(DuplicateProtocolIdError, match="protocol_id"):
            write_pull(db_path, rows, PULLED_AT)

        assert query(db_path, "SELECT COUNT(*) FROM protocol_content") == [(0,)]

    def test_write_returns_the_diff_it_applied(self, db_path):
        initialize_db(db_path)
        write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1), make_protocol(2)]
        ), PULLED_AT), PULLED_AT)

        diff = write_pull(db_path, format_entry(process_protocols(
            [make_protocol(1), make_protocol(3), make_protocol(2, title="Edited")]
        ), LATER), LATER)

        assert diff == {
            "new": ["3"], "changed": ["2"], "unchanged": ["1"], "absent": [],
        }
