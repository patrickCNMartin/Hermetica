# -----------------------------------------------------------------------------#
# TESTS — the v4 workspace search and what it selects
# -----------------------------------------------------------------------------#
"""The workspace route exists because `/v3/protocols` collapses a version family
to one item and cannot see trash or another member's published protocols. Every
assertion here defends one of those, plus the traps the sweep itself carries:
it is 1-indexed, it wraps its answer in `payload`, and its `next_page` is a URL
rather than a page number, so only truthiness can drive the loop.

The pager underneath is `fetch_pages`, tested against the protocol list in
test_request.py. What is tested here is the workspace route on top of it."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
import responses

from sources.protocols_io.config import FIRST_SEARCH_PAGE, PROTOCOL_TYPE_ID
from sources.protocols_io.discover import (
    IncompleteDiscoveryError,
    as_workspace_item,
    discover,
    search_workspace,
    search_workspace_items,
    select_protocols,
)

BASE_URL = "https://api.example.org"
WORKSPACE_URL = f"{BASE_URL}/v4/filemanager/workspaces/institute/search"
LIST_URL = f"{BASE_URL}/v3/protocols"
HEADERS = {"Authorization": "Bearer test-token"}

# Ids the fixture workspace holds, named so a failure reads as a story.
LIVE = [101, 102]
FAMILY_SIBLING = 103
PUBLIC = 104
PRIVATE = 105
COLLECTION = 106
TRASHED = 107
UNDER_TRASHED_FOLDER = 108

EVERY_PROTOCOL = {
    *LIVE,
    FAMILY_SIBLING,
    PUBLIC,
    PRIVATE,
    COLLECTION,
    TRASHED,
    UNDER_TRASHED_FOLDER,
}


# -----------------------------------------------------------------------------#
# MOCK WIRING
# -----------------------------------------------------------------------------#
def query(request) -> dict[str, list[str]]:
    return parse_qs(urlsplit(request.url).query)


def mount(workspace_records):
    """Serve the fixture workspace over the one endpoint the route calls."""
    pages = workspace_records["search_pages"]

    def search(request):
        page = int(query(request)["page_id"][0])
        return (200, {}, json.dumps(pages[page - FIRST_SEARCH_PAGE]))

    responses.add_callback(responses.GET, WORKSPACE_URL, callback=search)


@pytest.fixture
def found(workspace_records):
    """Every protocol the fixture workspace holds, as the route reads them."""

    @responses.activate
    def _found():
        mount(workspace_records)
        return search_workspace(HEADERS, WORKSPACE_URL)

    return _found()


@pytest.fixture
def items(workspace_records):
    """The protocol-content items, gated but not yet selected."""
    return [
        as_workspace_item(item)
        for page in workspace_records["search_pages"]
        for item in page["payload"]["items"]
        if item["content_type_id"] == 1
    ]


# -----------------------------------------------------------------------------#
# 1. THE SWEEP
# -----------------------------------------------------------------------------#
class TestWorkspaceSweep:
    def test_the_search_is_one_indexed(self):
        """0-indexing the search asks for a page the server does not have."""
        assert FIRST_SEARCH_PAGE == 1

    @responses.activate
    def test_the_first_request_asks_for_page_one(self, workspace_records):
        mount(workspace_records)

        search_workspace_items(WORKSPACE_URL, HEADERS)

        assert "page_id=1" in responses.calls[0].request.url

    @responses.activate
    def test_it_follows_next_page_to_the_end(self, workspace_records):
        """`next_page` is a URL here, not a page number — truthiness is the test."""
        mount(workspace_records)

        search_workspace_items(WORKSPACE_URL, HEADERS)

        asked = [int(query(call.request)["page_id"][0]) for call in responses.calls]
        assert asked == list(range(1, len(workspace_records["search_pages"]) + 1))

    @responses.activate
    def test_it_reads_through_the_payload_envelope(self, workspace_records):
        """v4 nests items under `payload`; a v3-shaped reader finds nothing."""
        mount(workspace_records)

        assert search_workspace_items(WORKSPACE_URL, HEADERS)

    @responses.activate
    def test_it_returns_the_whole_workspace_flat(self, workspace_records):
        """Folders, protocols, records and files in one list — no folder opened."""
        mount(workspace_records)

        swept = search_workspace_items(WORKSPACE_URL, HEADERS)

        assert {i["content_type_id"] for i in swept} > {1, 10}
        assert len(swept) == sum(
            len(p["payload"]["items"]) for p in workspace_records["search_pages"]
        )

    @responses.activate
    def test_the_page_size_asked_for_is_the_one_sent(self, workspace_records):
        mount(workspace_records)

        search_workspace_items(WORKSPACE_URL, HEADERS, page_size=25)

        assert query(responses.calls[0].request)["page_size"] == ["25"]

    @responses.activate
    def test_a_short_sweep_is_an_error(self, workspace_records):
        """The v4 search publishes a global total, so a missing branch is
        catchable here — which the folder-by-folder route never could."""
        page = json.loads(json.dumps(workspace_records["search_pages"][0]))
        page["payload"]["pagination"]["next_page"] = None
        responses.add(responses.GET, WORKSPACE_URL, json=page)

        with pytest.raises(IncompleteDiscoveryError):
            search_workspace_items(WORKSPACE_URL, HEADERS)

    @responses.activate
    def test_an_endpoint_that_reports_no_total_is_not_second_guessed(self):
        """No total, no completeness claim — a raise here would invent one."""
        responses.add(
            responses.GET,
            WORKSPACE_URL,
            json={"payload": {"items": [{"id": 1}], "pagination": None}},
        )

        assert len(search_workspace_items(WORKSPACE_URL, HEADERS)) == 1


# -----------------------------------------------------------------------------#
# 2. THE ITEM — discovery keeps what gates an id, and nothing else
# -----------------------------------------------------------------------------#
class TestWorkspaceItem:
    def test_it_keeps_only_the_gate_fields(self, workspace_records):
        raw = next(
            i
            for i in workspace_records["search_pages"][0]["payload"]["items"]
            if i["content_type_id"] == 1
        )

        item = as_workspace_item(raw)

        assert item._fields == ("id", "title", "type_id", "in_trash")
        assert item.id == raw["id"]

    def test_a_missing_trash_flag_is_not_trashed(self):
        """Absent is not True — the by-ID payload carries no `in_trash` at all."""
        assert as_workspace_item({"id": 1}).in_trash is False

    def test_only_a_literal_true_is_trashed(self):
        """A truthy string from a looser upstream must not retire a protocol."""
        assert as_workspace_item({"id": 1, "in_trash": "false"}).in_trash is False


# -----------------------------------------------------------------------------#
# 3. THE ROUTE — what reaches the pull
# -----------------------------------------------------------------------------#
class TestSearchWorkspace:
    def test_every_protocol_is_accounted_for(self, found):
        """One sweep reaches all of them; each is either pulled or explained."""
        assert (
            set(found.ids)
            | set(found.detail["trashed"])
            | set(found.detail["excluded"])
            == EVERY_PROTOCOL
        )

    def test_folders_are_never_returned_as_protocols(self, found):
        """Folder ids are four digits; a folder leaking through would show."""
        assert all(pid < 1000 for pid in found.ids)

    def test_a_record_or_a_file_is_never_reached_at_all(self, found):
        """content_type_id gates first, so these never reach type_id or trash —
        they are not excluded protocols, they were never protocols."""
        assert found.detail["workspace_protocols"] == len(EVERY_PROTOCOL)
        seen = (
            set(found.ids)
            | set(found.detail["trashed"])
            | set(found.detail["excluded"])
        )
        assert not any(pid > 1000 for pid in seen)

    def test_it_names_its_strategy(self, found):
        assert found.strategy == "workspace"

    def test_the_detail_counts_both_halves_of_the_sweep(self, found, workspace_records):
        swept = sum(
            len(p["payload"]["items"]) for p in workspace_records["search_pages"]
        )
        assert found.detail["workspace_items"] == swept
        assert found.detail["workspace_protocols"] < swept

    def test_a_private_protocol_nobody_shared_is_returned(self, found):
        """The whole reason this route is the default."""
        assert PRIVATE in found.ids

    def test_a_published_protocol_is_returned(self, found):
        assert PUBLIC in found.ids

    def test_both_members_of_a_version_family_are_returned(self, found):
        """`/v3/protocols` collapses these two to one. This route must not."""
        assert {LIVE[0], FAMILY_SIBLING} <= set(found.ids)

    def test_a_trashed_protocol_is_reported_but_not_returned(self, found):
        assert TRASHED not in found.ids
        assert TRASHED in found.detail["trashed"]

    def test_a_collection_is_reported_but_not_returned(self, found):
        assert COLLECTION not in found.ids
        assert COLLECTION in found.detail["excluded"]
        assert any(str(COLLECTION) in w for w in found.detail["warnings"])


# -----------------------------------------------------------------------------#
# 4. SELECTION
# -----------------------------------------------------------------------------#
class TestSelection:
    def test_every_live_protocol_is_selected(self, items):
        selection = select_protocols(items)

        assert set(LIVE) <= {i.id for i in selection.selected}

    def test_trash_is_upstreams_flag_and_nothing_else(self, items):
        selection = select_protocols(items)

        assert [i.id for i in selection.trashed] == [TRASHED]

    def test_a_collection_is_not_sealed_as_a_protocol(self, items):
        """type_id 3 is a Collection. Only type_id 1 is a protocol."""
        collection = next(i for i in items if i.id == COLLECTION)
        assert collection.type_id != PROTOCOL_TYPE_ID

        selection = select_protocols(items)

        assert COLLECTION in {i.id for i in selection.excluded}

    def test_an_item_with_no_type_id_is_still_selected(self):
        """The list response thins `type_id` to None. Unknown is not refused —
        only a type_id that is present and says something else."""
        thin = as_workspace_item({"id": 999, "type_id": None})

        assert select_protocols([thin]).selected == [thin]

    def test_the_exclusion_says_which_id_and_why(self, items):
        selection = select_protocols(items)

        warning = next(w for w in selection.warnings if str(COLLECTION) in w)
        assert "type_id 3" in warning

    def test_a_trashed_protocol_is_not_warned_about(self, items):
        """Trash is a normal outcome, not something nobody acted on."""
        selection = select_protocols(items)

        assert not any(str(TRASHED) in w for w in selection.warnings)

    def test_selected_trashed_and_excluded_partition_the_sweep(self, items):
        selection = select_protocols(items)

        counted = (
            len(selection.selected) + len(selection.trashed) + len(selection.excluded)
        )
        assert counted == len(items)

    def test_a_protocol_under_a_trashed_folder_is_selected(self, items):
        """KNOWN GAP, pinned here so it cannot change silently.

        The fixture's 108 sits inside a folder carrying `in_trash: True` while
        carrying `in_trash: False` itself. The walk this route replaced let
        folder position win; the sweep reads the protocol's own flag and nothing
        else, so 108 is pulled and sealed. Whether upstream ever produces that
        disagreement is unmeasured — `probe_trash_branch.py` is the probe that
        would settle it. If it does, this test is the one that has to change.
        """
        selection = select_protocols(items)

        assert UNDER_TRASHED_FOLDER in {i.id for i in selection.selected}


# -----------------------------------------------------------------------------#
# 5. ROUTING
# -----------------------------------------------------------------------------#
class TestDiscoverRouting:
    @responses.activate
    def test_the_workspace_strategy_sweeps(self, workspace_records):
        mount(workspace_records)

        discovered = discover("workspace", LIST_URL, HEADERS, WORKSPACE_URL)

        assert discovered.strategy == "workspace"
        assert set(discovered.ids) == EVERY_PROTOCOL - {TRASHED, COLLECTION}

    def test_the_workspace_strategy_without_a_uri_refuses(self):
        """No uri, no sweep — and an empty pull deprecates a whole platform."""
        with pytest.raises(ValueError, match="WORKSPACE_ID"):
            discover("workspace", LIST_URL, HEADERS, workspace_url="")

    @responses.activate
    def test_the_filter_strategy_declares_itself_degraded(self, list_items):
        responses.add(
            responses.GET,
            LIST_URL,
            json={"items": list_items(2), "pagination": {"next_page": None}},
        )

        discovered = discover("filter", LIST_URL, HEADERS)

        assert discovered.strategy == "filter"
        assert discovered.detail["degraded"] is True

    def test_an_unknown_strategy_is_refused(self):
        with pytest.raises(ValueError, match="unknown PULL_STRATEGY"):
            discover("walk", LIST_URL, HEADERS, WORKSPACE_URL)
