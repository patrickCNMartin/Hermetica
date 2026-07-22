# -----------------------------------------------------------------------------#
# TESTS — protocols.io pull → strip/hash → sqlite pipeline
# -----------------------------------------------------------------------------#
import sqlite3

import pytest
import responses

from chronos.utils.request_utils import (
    blob_protocol,
    get_protocol_list,
    process_protocols,
    strip_protocol,
)
from chronos.utils.db import initialize_db, insert_protocols, to_rows

# -----------------------------------------------------------------------------#
# CONSTANTS / FIXTURES
# -----------------------------------------------------------------------------#
BASE_URL = "https://api.protocols.io"
HEADERS = {"Authorization": "Bearer test-token"}
PROTOCOLS_URL = f"{BASE_URL}/v3/protocols"

# Fields the strip step must drop: traffic stats, publication flags, and the
# request-time signed-URL fields (image/versions) that would otherwise poison
# the content hash. Keep this in sync with strip_protocol's default exclude.
VOLATILE_FIELDS = ["stats", "published_on", "public", "peer_reviewed",
                   "image", "versions"]
# Fields that identify a protocol and must survive stripping.
# We will explicitely call which fields we want to avoid pulling anything unrequired
# and to make sure that it is stable should things change. 
# we want the system to fail if fields change since that could lead to downstream bugs
# created_on could be removed. It also means that if there is 
# another system that is not protocols.io we can pull relevant fields
# Could use metaSolid for this I guess...
STABLE_FIELDS = ["id", "guid", "title","description","doi","created_on",
                 "uri", "materials","materials_text","units","warning"]


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
        "materials" : [],
        "materials_text" : "This is how you do this protocol",
        "units" : [{"unit" : "L","used": "yeah"}],
        "warning" : "null", # Not sure if this is needed yet.
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
        """A pull issues a GET to /v3/protocols and returns the items."""
        responses.add(
            responses.GET, PROTOCOLS_URL,
            json={"items": [make_protocol(1)]}, status=200,
        )
        # Check that function return a list of protcols 
        result = get_protocol_list(BASE_URL, HEADERS)

        assert len(responses.calls) == 1
        assert responses.calls[0].request.url.startswith(PROTOCOLS_URL)
        assert len(result) == 1
        assert result[0]["id"] == 1

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

        result = get_protocol_list(BASE_URL, HEADERS, page_size=10)

        assert len(responses.calls) == 2
        assert len(result) == 13
        # The second request advanced page_id.
        assert "page_id=2" in responses.calls[1].request.url

    @responses.activate
    def test_stops_on_empty_page(self):
        """An empty items list ends pagination without error."""
        page_1 = [make_protocol(i) for i in range(10)]
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": page_1})
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": []})

        result = get_protocol_list(BASE_URL, HEADERS, page_size=10)

        assert len(responses.calls) == 2
        assert len(result) == 10

    @responses.activate
    def test_stops_at_max_page(self):
        """max_pull caps the number of pages fetched, regardless of server."""
        page = [make_protocol(i) for i in range(10)]
        responses.add(responses.GET, PROTOCOLS_URL, json={"items": page})  # repeats for every request

        results = get_protocol_list(BASE_URL, HEADERS, page_size=10, max_pull=5)
        assert len(responses.calls) == 5
        assert len(results) == 50

        before = len(responses.calls)
        results = get_protocol_list(BASE_URL, HEADERS, page_size=10, max_pull=3)
        assert len(responses.calls) - before == 3
        assert len(results) == 30




# -----------------------------------------------------------------------------#
# 3. PULLING THE SAME FIELDS  (stripping + hash stability)
# -----------------------------------------------------------------------------#
class TestFieldStripping:
    def test_volatile_fields_are_dropped(self):
        stripped = strip_protocol(make_protocol(1), None)
        for field in VOLATILE_FIELDS:
            assert field not in stripped, f"{field} should have been stripped"

    def test_stable_fields_survive(self):
        stripped = strip_protocol(make_protocol(1), None)
        for field in STABLE_FIELDS:
            assert field in stripped, f"{field} should have been kept"

    def test_hash_is_deterministic(self):
        """Same content -> same hash (canonical serialization)."""
        a = blob_protocol(strip_protocol(make_protocol(1), None))
        b = blob_protocol(strip_protocol(make_protocol(1), None))
        assert a == b

    def test_hash_ignores_signed_url_noise(self):
        """The whole point: image/versions/stats noise must NOT change the hash."""
        p1 = make_protocol(1)
        p2 = make_protocol(1)
        p2["image"] = {"placeholder": "https://files.x/y.jpg?Policy=DIFFERENT"}
        p2["versions"] = [{"id": 1, "image": {"source": "?Policy=DIFFERENT"}}]
        p2["stats"] = {"number_of_views": 999999}

        h1 = blob_protocol(strip_protocol(p1, None))
        h2 = blob_protocol(strip_protocol(p2, None))
        assert h1 == h2

    def test_real_content_change_changes_hash(self):
        """A genuine change to semantic content DOES produce a new hash."""
        h1 = blob_protocol(strip_protocol(make_protocol(1, title="Original"), None))
        h2 = blob_protocol(strip_protocol(make_protocol(1, title="Edited"), None))
        assert h1 != h2

    def test_process_protocols_dedupes_by_hash(self):
        """Identical duplicates collapse; distinct protocols stay separate."""
        protocols = [make_protocol(1), make_protocol(1), make_protocol(2)]
        processed = process_protocols(protocols)
        assert len(processed) == 2  # id-1 dup collapsed, id-2 distinct


# -----------------------------------------------------------------------------#
# 4. A DATABASE BEING BUILT
# -----------------------------------------------------------------------------#
class TestDatabaseBuild:
    EXPECTED_COLUMNS = [
        "hash", "protocol_id", "protocol_name", "protocol_guid",
        "protocol", "valid_from", "deprecated_at",
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
            "valid_from, deprecated_at FROM protocol_versions"
        ).fetchone()
        assert row[0] == "1"
        assert row[1] == "My Protocol"
        assert row[2] == f"{1:032X}"
        assert row[3] == "2025-04-29"   # from created_on
        assert row[4] is None            # deprecated_at unset for a live version

    def test_insert_is_idempotent(self, db_path):
        """Re-inserting the same content is a no-op (content-hash primary key)."""
        rows = to_rows(process_protocols([make_protocol(1), make_protocol(2)]))
        initialize_db(db_path)
        # Is this enough to make sure that new elements are added and other discarded
        assert insert_protocols(db_path, rows) == 2
        assert insert_protocols(db_path, rows) == 0  # nothing new the second time

