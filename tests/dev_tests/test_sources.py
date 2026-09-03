# -----------------------------------------------------------------------------#
# TESTS — the source interface
# -----------------------------------------------------------------------------#
"""Two implementations, so the seam is checked rather than assumed: the real
protocols.io adapter, and a fake built here from prebuilt artefacts. chronos
must not be able to tell them apart."""

import copy
import json

import pytest
import responses

from chronos.chronos import build_sources, run_pull
from seal.store import HISTORY_TABLE, ID_COLUMN, SCHEMA
from sources.contract import (
    DiscoveredProtocols,
    FetchedProtocol,
    ProtocolSource,
    UnreadableProtocolError,
    check_source_name,
)
from sources.protocols_io import build_source
from sources.protocols_io.artefact import build_protocol_artefact
from utils.dates import to_epoch
from utils.intervals import active_hashes
from utils.store import connect, initialize_db

PULLED_AT = to_epoch("2026-07-27")

BASE_URL = "https://api.example.org"
LIST_URL = f"{BASE_URL}/v3/protocols"
PROTOCOL_URL = f"{BASE_URL}/v4/protocols/"


# -----------------------------------------------------------------------------#
# A FAKE SOURCE — the second implementation
# -----------------------------------------------------------------------------#
def fake_source(artefacts, retired=(), unreadable=(), name="fake", warnings=()):
    """A source built from artefacts already in hand. No network, no platform."""
    by_id = {a.id: a for a in artefacts}
    ids = list(by_id) + list(retired) + list(unreadable)

    def _discover():
        return DiscoveredProtocols(ids, "fake", {"selected": len(ids)})

    def _fetch(protocol_id):
        if protocol_id in retired:
            return FetchedProtocol(None, True, list(warnings))
        if protocol_id in unreadable:
            return FetchedProtocol(None, False, list(warnings))
        return FetchedProtocol(by_id[protocol_id], False, list(warnings))

    return ProtocolSource(name, _discover, _fetch)


@pytest.fixture
def artefacts(by_id_records):
    return [build_protocol_artefact(copy.deepcopy(r)) for r in by_id_records.values()]


# -----------------------------------------------------------------------------#
# 1. THE SOURCE NAME
# -----------------------------------------------------------------------------#
class TestCheckSourceName:
    @pytest.mark.parametrize("name", ["protocols_io", "kantele", "a1", "x_9_y"])
    def test_a_plain_lowercase_name_is_accepted(self, name):
        assert check_source_name(name) == name

    @pytest.mark.parametrize(
        "name", ["protocols:io", "Protocols_IO", "proto io", "proto-io", "", None]
    )
    def test_anything_that_would_break_a_uid_is_refused(self, name):
        """The name prefixes protocol_uid, so a separator in it is ambiguous."""
        with pytest.raises(ValueError):
            check_source_name(name)


# -----------------------------------------------------------------------------#
# 2. ONE PULL, DRIVEN BY A FAKE SOURCE
# -----------------------------------------------------------------------------#
class TestRunPull:
    def test_every_artefact_is_sealed(self, db_path, artefacts):
        initialize_db(db_path, SCHEMA)

        entry = run_pull(db_path, PULLED_AT, fake_source(artefacts))

        assert entry["sealed"] == len(artefacts)
        with connect(db_path) as conn:
            assert len(active_hashes(conn, HISTORY_TABLE, ID_COLUMN)) == len(artefacts)

    def test_the_entry_names_its_source(self, db_path, artefacts):
        initialize_db(db_path, SCHEMA)

        entry = run_pull(db_path, PULLED_AT, fake_source(artefacts, name="kantele"))

        assert entry["source"] == "kantele"

    def test_a_retired_protocol_is_not_sealed(self, db_path, artefacts):
        initialize_db(db_path, SCHEMA)

        entry = run_pull(db_path, PULLED_AT, fake_source(artefacts, retired=[9001]))

        assert entry["deprecated"] == [9001]
        assert entry["sealed"] == len(artefacts)

    def test_warnings_reach_the_entry(self, db_path, artefacts):
        initialize_db(db_path, SCHEMA)

        entry = run_pull(
            db_path, PULLED_AT, fake_source(artefacts[:1], warnings=["look at me"])
        )

        assert "look at me" in entry["warnings"]

    def test_an_unreadable_protocol_stops_the_pull(self, db_path, artefacts):
        """Failing to read one is not evidence it went away, and nothing may
        deprecate it by absence. Until skipped is wired through _diff, the only
        safe answer is to write nothing."""
        initialize_db(db_path, SCHEMA)

        with pytest.raises(UnreadableProtocolError):
            run_pull(db_path, PULLED_AT, fake_source(artefacts, unreadable=[9002]))

    def test_a_stopped_pull_writes_nothing(self, db_path, artefacts):
        initialize_db(db_path, SCHEMA)

        with pytest.raises(UnreadableProtocolError):
            run_pull(db_path, PULLED_AT, fake_source(artefacts, unreadable=[9002]))

        with connect(db_path) as conn:
            assert active_hashes(conn, HISTORY_TABLE, ID_COLUMN) == {}

    def test_a_source_name_that_breaks_uids_is_refused(self, db_path, artefacts):
        initialize_db(db_path, SCHEMA)

        with pytest.raises(ValueError):
            run_pull(db_path, PULLED_AT, fake_source(artefacts, name="bad:name"))


# -----------------------------------------------------------------------------#
# 3. THE PROTOCOLS.IO ADAPTER THROUGH THE SAME INTERFACE
# -----------------------------------------------------------------------------#
class TestBuildSource:
    def mount(self, records):
        ids = [r["id"] for r in records]
        responses.add(
            responses.GET,
            LIST_URL,
            json={
                "items": [{"id": i} for i in ids],
                "pagination": {"total_results": len(ids), "next_page": None},
            },
        )
        for record in records:
            responses.add(
                responses.GET, f"{PROTOCOL_URL}{record['id']}", json={"payload": record}
            )

    def source(self, **kwargs):
        return build_source(base_url=BASE_URL, api_key="k", strategy="filter", **kwargs)

    def test_it_is_named_for_its_platform(self):
        assert self.source().name == "protocols_io"

    @responses.activate
    def test_discover_yields_ids(self, by_id_records):
        self.mount(list(by_id_records.values()))

        discovered = self.source().discover()

        assert sorted(discovered.ids) == sorted(r["id"] for r in by_id_records.values())

    @responses.activate
    def test_fetch_returns_an_artefact(self, by_id_records):
        record = copy.deepcopy(by_id_records["baseline"])
        self.mount([record])

        fetched = self.source().fetch(record["id"])

        assert not fetched.retired
        assert fetched.artefact.id == record["id"]

    @responses.activate
    def test_a_deprecated_keyword_retires_without_an_artefact(self, by_id_records):
        record = copy.deepcopy(by_id_records["baseline"])
        record["keywords"] = "sp3, deprecated"
        self.mount([record])

        fetched = self.source().fetch(record["id"])

        assert fetched.retired
        assert fetched.artefact is None

    @responses.activate
    def test_a_pull_through_the_real_adapter_seals(self, db_path, by_id_records):
        """The whole seam, end to end: chronos drives protocols.io the same way
        it drives the fake."""
        self.mount([copy.deepcopy(r) for r in by_id_records.values()])
        initialize_db(db_path, SCHEMA)

        entry = run_pull(db_path, PULLED_AT, self.source())

        assert entry["source"] == "protocols_io"
        assert entry["sealed"] == len(by_id_records)

    @responses.activate
    def test_the_raw_dump_holds_one_record_per_line(self, tmp_path, by_id_records):
        """Line delimited so a pull that dies halfway leaves what it read."""
        records = [copy.deepcopy(r) for r in by_id_records.values()]
        self.mount(records)
        source = self.source(raw_dump=str(tmp_path))

        for protocol_id in source.discover().ids:
            source.fetch(protocol_id)

        dump = (tmp_path / "protocols_io_raw.jsonl").read_text().splitlines()
        assert len(dump) == len(records)
        assert {json.loads(line)["id"] for line in dump} == {r["id"] for r in records}

    @responses.activate
    def test_the_dump_is_truncated_when_the_source_is_built(self, tmp_path):
        (tmp_path / "protocols_io_raw.jsonl").write_text("stale\n")

        self.source(raw_dump=str(tmp_path))

        assert (tmp_path / "protocols_io_raw.jsonl").read_text() == ""


# -----------------------------------------------------------------------------#
# 4. CHOOSING SOURCES
# -----------------------------------------------------------------------------#
class TestBuildSources:
    """Testable because every value is an argument: nothing is read from the
    module, so there is no frame to patch."""

    def test_a_known_name_is_configured(self):
        sources = build_sources(["protocols_io"], base_url=BASE_URL, api_key="k")

        assert [s.name for s in sources] == ["protocols_io"]

    def test_names_keep_their_order(self):
        names = ["protocols_io", "protocols_io"]

        sources = build_sources(names, base_url=BASE_URL, api_key="k")

        assert [s.name for s in sources] == names

    def test_an_empty_list_pulls_nothing(self):
        assert build_sources([], base_url=BASE_URL, api_key="k") == []

    def test_an_unknown_name_is_refused(self):
        """Better to stop than to run a night's pull against fewer sources than
        the operator asked for."""
        with pytest.raises(ValueError, match="unknown source"):
            build_sources(["kantele"], base_url=BASE_URL, api_key="k")
