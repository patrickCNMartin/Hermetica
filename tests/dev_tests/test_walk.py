# -----------------------------------------------------------------------------#
# TESTS — the File Manager workspace walk and what it selects
# -----------------------------------------------------------------------------#
"""The walk exists because `/v3/protocols` collapses a version family to one
item and cannot see trash or another member's published protocols. Every
assertion here defends one of those, plus the pager trap: this endpoint is
1-indexed while the protocol list is 0-indexed, and asking it for page 0 returns
an empty array *with* a populated next_page."""

import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest
import responses

from chronos.utils.filemanager_utils import (
    FIRST_FOLDER_PAGE,
    IncompleteWalkError,
    fetch_folder_ids,
    select_protocols,
    walk_workspace,
)

BASE_URL = "https://api.example.org"
HEADERS = {"Authorization": "Bearer test-token"}

# Ids the fixture tree resolves to, named so a failure reads as a story.
SHARED = [101, 102]
FAMILY_SIBLING = 103
PUBLIC_NOT_SHARED = 104
PRIVATE_NOT_SHARED = 105
COLLECTION = 106
TRASHED = 107
UNDER_TRASHED_FOLDER = 108


# -----------------------------------------------------------------------------#
# MOCK WIRING
# -----------------------------------------------------------------------------#
def query(request) -> dict[str, list[str]]:
    return parse_qs(urlsplit(request.url).query)


def mount(walk_records):
    """Serve the fixture tree over the three endpoints the walk calls."""
    responses.add(
        responses.GET,
        f"{BASE_URL}/v3/filemanager/folders",
        json=walk_records["top_folders"],
    )

    for guid, pages in walk_records["folder_pages"].items():

        def pager(request, pages=pages):
            page = int(query(request)["page_id"][0])
            return (200, {}, json.dumps(pages[page - FIRST_FOLDER_PAGE]))

        responses.add_callback(
            responses.GET, f"{BASE_URL}/v3/folders/{guid}/ids", callback=pager
        )

    def items(request):
        wanted = query(request).get("ids[]", [])
        found = [walk_records["items"][i] for i in wanted if i in walk_records["items"]]
        return (200, {}, json.dumps({"items": found, "status_code": 0}))

    responses.add_callback(
        responses.GET, f"{BASE_URL}/v3/filemanager/items", callback=items
    )


@pytest.fixture
def walked(walk_records):
    """The fixture tree, walked once."""

    @responses.activate
    def _walk():
        mount(walk_records)
        return walk_workspace(BASE_URL, HEADERS)

    return _walk()


# -----------------------------------------------------------------------------#
# 1. THE PAGER
# -----------------------------------------------------------------------------#
class TestFolderPager:
    def test_it_starts_at_page_one_not_zero(self):
        """0-indexing this endpoint returns an empty page and exits cleanly."""
        assert FIRST_FOLDER_PAGE == 1

    @responses.activate
    def test_the_first_request_asks_for_page_one(self, walk_records):
        mount(walk_records)
        guid = next(iter(walk_records["folder_pages"]))

        fetch_folder_ids(BASE_URL, HEADERS, guid)

        assert "page_id=1" in responses.calls[0].request.url

    @responses.activate
    def test_it_follows_next_page_to_the_end(self, walk_records):
        mount(walk_records)
        guid, pages = next(
            (g, p) for g, p in walk_records["folder_pages"].items() if len(p) > 1
        )

        ids = fetch_folder_ids(BASE_URL, HEADERS, guid)

        assert ids == [i for page in pages for i in page["ids"]]
        assert len(ids) == pages[0]["pagination"]["total_results"]

    @responses.activate
    def test_a_short_read_raises_rather_than_looking_empty(self):
        """No global total exists, so the per-folder count is the only guard."""
        responses.add(
            responses.GET,
            f"{BASE_URL}/v3/folders/SHORT/ids",
            json={
                "ids": [1, 2],
                "pagination": {"next_page": None, "total_results": 9},
                "status_code": 0,
            },
        )

        with pytest.raises(IncompleteWalkError, match=r"yielded 2 item ids.*reports 9"):
            fetch_folder_ids(BASE_URL, HEADERS, "SHORT")

    @responses.activate
    def test_an_empty_folder_is_not_an_error(self, walk_records):
        mount(walk_records)
        guid = next(
            g for g, p in walk_records["folder_pages"].items() if p[0]["ids"] == []
        )

        assert fetch_folder_ids(BASE_URL, HEADERS, guid) == []


# -----------------------------------------------------------------------------#
# 2. THE WALK
# -----------------------------------------------------------------------------#
class TestWalk:
    def test_it_finds_every_protocol_in_the_tree(self, walked):
        assert {item.id for item in walked} == {
            101,
            102,
            103,
            104,
            105,
            106,
            107,
            108,
        }

    def test_folders_are_not_returned_as_protocols(self, walked):
        """Only content_type_id 1 is a protocol; 10 is a folder."""
        assert all(item.id < 1000 for item in walked)

    def test_a_protocol_in_two_folders_appears_once(self, walked):
        assert [item.id for item in walked].count(101) == 1

    def test_it_recurses_into_nested_folders(self, walked):
        """105 sits two levels down; a flat walk would miss it."""
        assert PRIVATE_NOT_SHARED in {item.id for item in walked}

    def test_the_path_records_where_each_protocol_was_found(self, walked):
        found = {item.id: item.path for item in walked}
        assert found[FAMILY_SIBLING].endswith("Bench")

    def test_a_protocol_in_the_trash_folder_is_trashed(self, walked):
        item = next(i for i in walked if i.id == TRASHED)
        assert item.in_trash is True
        assert item.trashed is True

    def test_a_protocol_under_a_trashed_folder_inherits_it(self, walked):
        """The case upstream had no example of: the folder is flagged, the
        protocol inside is not. Position wins, and the disagreement is visible."""
        item = next(i for i in walked if i.id == UNDER_TRASHED_FOLDER)
        assert item.in_trash is False
        assert item.trashed is True
        assert item.flag_disagrees is True

    def test_live_protocols_are_not_trashed(self, walked):
        assert not any(i.trashed for i in walked if i.id in SHARED)

    @responses.activate
    def test_each_folder_is_walked_once(self, walk_records):
        mount(walk_records)

        walk_workspace(BASE_URL, HEADERS)

        asked = [
            re.search(r"/v3/folders/([^/]+)/ids", call.request.url).group(1)
            for call in responses.calls
            if "/v3/folders/" in call.request.url
        ]
        # More requests than folders is fine — pagination — but a folder must
        # never be *restarted*, which is what a cycle would look like.
        assert asked.count(asked[0]) == len(walk_records["folder_pages"][asked[0]])


# -----------------------------------------------------------------------------#
# 3. SELECTION
# -----------------------------------------------------------------------------#
class TestSelection:
    def test_shared_protocols_are_selected(self, walked):
        selection = select_protocols(walked, SHARED)

        assert set(SHARED) <= {item.id for item in selection.selected}
        assert selection.admitted_by[101] == "shared"

    def test_a_family_sibling_of_a_shared_protocol_is_recovered(self, walked):
        """The list endpoint returns one item per family, so a shared protocol's
        other versions are invisible to every filter. Without this clause they
        would silently never be sealed."""
        selection = select_protocols(walked, SHARED)

        assert FAMILY_SIBLING in selection.admitted_by
        assert selection.admitted_by[FAMILY_SIBLING] == "shared family"

    def test_a_public_protocol_nobody_shared_is_selected(self, walked):
        """Published by another member: in no user-scoped filter at all."""
        selection = select_protocols(walked, SHARED)

        assert selection.admitted_by[PUBLIC_NOT_SHARED] == "public"

    def test_a_private_unshared_protocol_is_excluded(self, walked):
        selection = select_protocols(walked, SHARED)

        assert PRIVATE_NOT_SHARED in {item.id for item in selection.excluded}
        assert PRIVATE_NOT_SHARED not in selection.admitted_by

    def test_trashed_protocols_are_never_selected(self, walked):
        selection = select_protocols(walked, SHARED + [TRASHED])

        assert TRASHED in {item.id for item in selection.trashed}
        assert TRASHED not in {item.id for item in selection.selected}

    def test_a_trashed_protocol_that_is_shared_is_still_trashed(self, walked):
        """Trash wins over every admission route — it is checked first."""
        selection = select_protocols(walked, [TRASHED])

        assert TRASHED not in {item.id for item in selection.selected}
        assert TRASHED in {item.id for item in selection.trashed}
        assert TRASHED not in selection.admitted_by

    def test_a_collection_is_not_sealed_as_a_protocol(self, walked):
        selection = select_protocols(walked, SHARED + [COLLECTION])

        assert COLLECTION not in {item.id for item in selection.selected}
        assert any("not a protocol" in w for w in selection.warnings)

    def test_the_flag_disagreement_is_reported(self, walked):
        selection = select_protocols(walked, SHARED)

        assert any("in_trash flag disagrees" in w for w in selection.warnings)

    def test_nothing_shared_selects_only_public(self, walked):
        selection = select_protocols(walked, [])

        assert {item.id for item in selection.selected} == {PUBLIC_NOT_SHARED}

    def test_selected_trashed_and_excluded_partition_the_live_tree(self, walked):
        selection = select_protocols(walked, SHARED)
        counted = (
            len(selection.selected) + len(selection.trashed) + len(selection.excluded)
        )

        assert counted == len(walked)
