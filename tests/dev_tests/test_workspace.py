# -----------------------------------------------------------------------------#
# TESTS — the v4 workspace search and what it selects
# -----------------------------------------------------------------------------#
"""The workspace route exists because `/v3/protocols` collapses a version family
to one item and cannot see trash or another member's published protocols. Every
assertion here defends one of those, plus the pager traps: the search is
1-indexed and wraps its answer in `payload`, while the folder-ids endpoint is
1-indexed and the protocol list is 0-indexed. Asking the folder pager for page 0
returns an empty array *with* a populated next_page."""

import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest
import responses

from sources.protocols_io.config import FIRST_FOLDER_PAGE, FIRST_SEARCH_PAGE
from sources.protocols_io.discover import (
    IncompleteDiscoveryError,
    collect_protocols,
    fetch_folder_ids,
    folder_path,
    is_trash_folder,
    search_folders,
    select_protocols,
)

BASE_URL = "https://api.example.org"
WORKSPACE_URL = f"{BASE_URL}/v4/filemanager/workspaces/institute/search"
HEADERS = {"Authorization": "Bearer test-token"}

# Ids the fixture tree resolves to, named so a failure reads as a story.
LIVE = [101, 102]
FAMILY_SIBLING = 103
PUBLIC = 104
PRIVATE = 105
COLLECTION = 106
TRASHED = 107
UNDER_TRASHED_FOLDER = 108

TRASH_GUID = "AAAA00000000000000000000000000B3"
NESTED_GUID = "AAAA00000000000000000000000000B5"


# -----------------------------------------------------------------------------#
# MOCK WIRING
# -----------------------------------------------------------------------------#
def query(request) -> dict[str, list[str]]:
    return parse_qs(urlsplit(request.url).query)


def mount(workspace_records):
    """Serve the fixture workspace over the three endpoints discovery calls."""
    search_pages = workspace_records["search_pages"]

    def search(request):
        page = int(query(request)["page_id"][0])
        return (200, {}, json.dumps(search_pages[page - FIRST_SEARCH_PAGE]))

    responses.add_callback(responses.GET, WORKSPACE_URL, callback=search)

    for guid, pages in workspace_records["folder_pages"].items():

        def pager(request, pages=pages):
            page = int(query(request)["page_id"][0])
            return (200, {}, json.dumps(pages[page - FIRST_FOLDER_PAGE]))

        responses.add_callback(
            responses.GET, f"{BASE_URL}/v3/folders/{guid}/ids", callback=pager
        )

    def items(request):
        found = [
            workspace_records["items"][i]
            for i in query(request).get("ids[]", [])
            if i in workspace_records["items"]
        ]
        return (200, {}, json.dumps({"items": found, "status_code": 0}))

    responses.add_callback(
        responses.GET, f"{BASE_URL}/v3/filemanager/items", callback=items
    )


@pytest.fixture
def folders(workspace_records):
    """The fixture workspace's folder tree, swept once."""

    @responses.activate
    def _folders():
        mount(workspace_records)
        return search_folders(WORKSPACE_URL, HEADERS)

    return _folders()


@pytest.fixture
def found(workspace_records, folders):
    """Every protocol the fixture workspace holds."""

    @responses.activate
    def _found():
        mount(workspace_records)
        return collect_protocols(BASE_URL, HEADERS, folders)

    return _found()


# -----------------------------------------------------------------------------#
# 1. THE SEARCH SWEEP
# -----------------------------------------------------------------------------#
class TestWorkspaceSearch:
    def test_the_search_is_one_indexed(self):
        """0-indexing the search asks for a page the server does not have."""
        assert FIRST_SEARCH_PAGE == 1

    @responses.activate
    def test_the_first_request_asks_for_page_one(self, workspace_records):
        mount(workspace_records)

        search_folders(WORKSPACE_URL, HEADERS)

        assert "page_id=1" in responses.calls[0].request.url

    @responses.activate
    def test_it_follows_next_page_to_the_end(self, workspace_records):
        """`next_page` is a URL here, not a page number — truthiness is the test."""
        mount(workspace_records)

        search_folders(WORKSPACE_URL, HEADERS)

        asked = [
            int(query(call.request)["page_id"][0])
            for call in responses.calls
            if "/search" in call.request.url
        ]
        assert asked == list(range(1, len(workspace_records["search_pages"]) + 1))

    @responses.activate
    def test_it_reads_through_the_payload_envelope(self, workspace_records):
        """v4 nests items under `payload`; a v3-shaped reader finds nothing."""
        mount(workspace_records)

        assert search_folders(WORKSPACE_URL, HEADERS)

    def test_it_returns_every_folder(self, folders):
        assert len(folders) == 6
        assert all(f["content_type_id"] == 10 for f in folders)

    @responses.activate
    def test_a_short_sweep_is_an_error(self, workspace_records):
        """The v4 search publishes a global total — so a missing branch is
        catchable here, which the folder-by-folder route never could."""
        page = json.loads(json.dumps(workspace_records["search_pages"][0]))
        page["payload"]["pagination"]["next_page"] = None
        responses.add(responses.GET, WORKSPACE_URL, json=page)

        with pytest.raises(IncompleteDiscoveryError):
            search_folders(WORKSPACE_URL, HEADERS)


# -----------------------------------------------------------------------------#
# 2. THE TREE, REBUILT LOCALLY
# -----------------------------------------------------------------------------#
class TestFolderTree:
    def test_trash_is_found_by_default_id(self, folders):
        """Not by title — upstream is free to translate that."""
        trash = [f for f in folders if f["guid"] == TRASH_GUID][0]
        assert trash["default_id"] == 12
        assert is_trash_folder(trash) is True

    def test_the_trash_folder_reports_itself_as_not_trashed(self, folders):
        """Measured upstream: the container carries in_trash False."""
        trash = [f for f in folders if f["guid"] == TRASH_GUID][0]
        assert trash["in_trash"] is False

    def test_a_nested_folder_is_placed_by_its_parent_guid(self, folders):
        """B5 sits two levels down; only parent_guid says so."""
        index = {f["guid"]: f for f in folders}

        assert folder_path(NESTED_GUID, index) == "Institute/Bench/Sample"

    def test_a_parent_cycle_terminates(self, folders):
        """Upstream's to have, not ours to hang on."""
        index = {f["guid"]: dict(f) for f in folders}
        index[NESTED_GUID]["parent_guid"] = NESTED_GUID

        assert folder_path(NESTED_GUID, index) == "Sample"


# -----------------------------------------------------------------------------#
# 3. THE FOLDER PAGER
# -----------------------------------------------------------------------------#
class TestFolderPager:
    def test_it_starts_at_page_one_not_zero(self):
        """0-indexing this endpoint returns an empty page and exits cleanly."""
        assert FIRST_FOLDER_PAGE == 1

    @responses.activate
    def test_the_first_request_asks_for_page_one(self, workspace_records):
        mount(workspace_records)
        guid = next(iter(workspace_records["folder_pages"]))

        fetch_folder_ids(BASE_URL, HEADERS, guid)

        folder_calls = [c for c in responses.calls if "/v3/folders/" in c.request.url]
        assert "page_id=1" in folder_calls[0].request.url

    @responses.activate
    def test_it_follows_next_page_to_the_end(self, workspace_records):
        mount(workspace_records)
        guid, pages = next(
            (g, p) for g, p in workspace_records["folder_pages"].items() if len(p) > 1
        )

        ids = fetch_folder_ids(BASE_URL, HEADERS, guid)

        assert ids == [i for page in pages for i in page["ids"]]

    @responses.activate
    def test_an_empty_folder_is_not_an_error(self, workspace_records):
        mount(workspace_records)
        guid = next(
            g for g, p in workspace_records["folder_pages"].items() if p[0]["ids"] == []
        )

        assert fetch_folder_ids(BASE_URL, HEADERS, guid) == []

    @responses.activate
    def test_a_short_folder_read_is_an_error(self, workspace_records):
        """A short read must never pass as an empty folder."""
        mount(workspace_records)
        guid = next(iter(workspace_records["folder_pages"]))
        responses.replace(
            responses.GET,
            f"{BASE_URL}/v3/folders/{guid}/ids",
            json={
                "ids": [9001],
                "pagination": {"next_page": None, "total_results": 6},
                "status_code": 0,
            },
        )

        with pytest.raises(IncompleteDiscoveryError):
            fetch_folder_ids(BASE_URL, HEADERS, guid)


# -----------------------------------------------------------------------------#
# 4. WHAT THE WORKSPACE HOLDS
# -----------------------------------------------------------------------------#
class TestCollectedProtocols:
    def test_it_finds_every_protocol_in_the_tree(self, found):
        assert {item.id for item in found} == {
            *LIVE,
            FAMILY_SIBLING,
            PUBLIC,
            PRIVATE,
            COLLECTION,
            TRASHED,
            UNDER_TRASHED_FOLDER,
        }

    def test_folders_are_not_returned_as_protocols(self, found):
        """Folder ids are four digits; a folder leaking through would show."""
        assert all(item.id < 1000 for item in found)

    def test_a_protocol_in_two_folders_appears_once(self, found):
        assert [item.id for item in found].count(101) == 1

    def test_it_reaches_into_nested_folders(self, found):
        """105 sits two levels down; a flat sweep would miss it."""
        assert PRIVATE in {item.id for item in found}

    def test_the_path_records_where_each_protocol_was_found(self, found):
        paths = {item.id: item.path for item in found}
        assert paths[FAMILY_SIBLING].endswith("Bench")

    def test_a_protocol_in_the_trash_folder_is_trashed(self, found):
        item = next(i for i in found if i.id == TRASHED)
        assert item.in_trash is True
        assert item.trashed is True

    def test_a_protocol_under_a_trashed_folder_inherits_it(self, found):
        """The case upstream had no example of: the folder is flagged, the
        protocol inside is not. Position wins, and the disagreement is visible."""
        item = next(i for i in found if i.id == UNDER_TRASHED_FOLDER)
        assert item.in_trash is False
        assert item.trashed is True
        assert item.flag_disagrees is True

    def test_live_protocols_are_not_trashed(self, found):
        assert not any(i.trashed for i in found if i.id in LIVE)

    @responses.activate
    def test_each_folder_is_listed_once(self, workspace_records, folders):
        mount(workspace_records)

        collect_protocols(BASE_URL, HEADERS, folders)

        asked = [
            re.search(r"/v3/folders/([^/]+)/ids", call.request.url).group(1)
            for call in responses.calls
            if "/v3/folders/" in call.request.url
        ]
        # More requests than folders is fine — pagination — but a folder must
        # never be *restarted*, which is what a cycle would look like.
        assert asked.count(asked[0]) == len(workspace_records["folder_pages"][asked[0]])


# -----------------------------------------------------------------------------#
# 5. SELECTION
# -----------------------------------------------------------------------------#
class TestSelection:
    def test_every_live_protocol_is_selected(self, found):
        selection = select_protocols(found)

        assert set(LIVE) <= {i.id for i in selection.selected}

    def test_a_private_protocol_nobody_shared_is_selected(self, found):
        """The whole reason this route is the default."""
        selection = select_protocols(found)

        assert PRIVATE in {i.id for i in selection.selected}

    def test_a_public_protocol_is_selected(self, found):
        selection = select_protocols(found)

        assert PUBLIC in {i.id for i in selection.selected}

    def test_trashed_protocols_are_never_selected(self, found):
        selection = select_protocols(found)

        assert TRASHED not in {i.id for i in selection.selected}

    def test_a_protocol_under_a_trashed_folder_is_never_selected(self, found):
        selection = select_protocols(found)

        assert UNDER_TRASHED_FOLDER not in {i.id for i in selection.selected}

    def test_a_collection_is_not_sealed_as_a_protocol(self, found):
        selection = select_protocols(found)

        assert COLLECTION in {i.id for i in selection.excluded}

    def test_the_flag_disagreement_is_reported(self, found):
        selection = select_protocols(found)

        assert any(str(UNDER_TRASHED_FOLDER) in w for w in selection.warnings)

    def test_selected_trashed_and_excluded_partition_the_tree(self, found):
        selection = select_protocols(found)

        counted = (
            len(selection.selected) + len(selection.trashed) + len(selection.excluded)
        )
        assert counted == len(found)
