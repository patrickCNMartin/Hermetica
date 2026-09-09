# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from typing import Iterable, NamedTuple

from sources.contract import DiscoveredProtocols
from sources.protocols_io.client import call_api, read_payload
from sources.protocols_io.config import (
    FIRST_PAGE,
    FIRST_SEARCH_PAGE,
    PROTOCOL_CONTENT_TYPE,
    PROTOCOL_TYPE_ID,
    SEARCH_PAGE_SIZE,
)


# -----------------------------------------------------------------------------#
# ERROR HANDLING
# -----------------------------------------------------------------------------#
class IncompleteDiscoveryError(RuntimeError):
    """A discovery read collected fewer records than the server said it holds."""

    def __init__(self, read: int, reported: int, refusing: str):
        self.read, self.reported = read, reported
        super().__init__(
            f"read {read} of {reported} reported; refusing to {refusing}"
        )


# -----------------------------------------------------------------------------#
# WORKSPACE ITEMS
# -----------------------------------------------------------------------------#
class WorkspaceItem(NamedTuple):
    """One protocol the workspace search returned.

    Discovery yields ids; the by-ID fetch is the only source of content. Only
    what gates an id or names it in a warning is kept.
    """

    id: int
    title: str | None
    type_id: int | None
    in_trash: bool


# -----------------------------------------------------------------------------#
# THE PAGER
# -----------------------------------------------------------------------------#
def fetch_pages(
    url: str,
    headers: dict,
    params: dict,
    start_page: int,
    page_size: int,
    max_pull: int | None = None,
) -> tuple[list[dict], int | None]:
    """Fetch pages until the server says stop. Returns (items, total_results)."""
    items: list[dict] = []
    total: int | None = None
    page = start_page

    while max_pull is None or (page - start_page) < max_pull:
        print(f"Processing Page: {page}")
        response = call_api(url, headers, {**params, "page_id": page})
        payload = read_payload(response)
        batch = payload.get("items") or []
        pagination = payload.get("pagination")

        if total is None and pagination:
            total = pagination.get("total_results")
        if not batch:
            break
        items.extend(batch)

        if pagination:
            if not pagination.get("next_page"):
                break
        elif len(batch) < page_size:
            break
        page += 1

    return items, total


# -----------------------------------------------------------------------------#
# ROUTE ONE — THE WORKSPACE SEARCH
# -----------------------------------------------------------------------------#
def search_workspace_items(
    workspace_url: str,
    headers: dict,
    page_size: int = SEARCH_PAGE_SIZE,
) -> list[dict]:
    """Every item in the workspace, flat, in one paginated sweep.

    The v4 search returns folders, protocols, records and files together, each
    protocol already carrying the id, `type_id` and `in_trash` a gate needs, so
    no folder is ever opened. It publishes a global total, making a short read
    catchable here rather than showing up later as a protocol gone missing.
    """
    items, total = fetch_pages(
        workspace_url, headers, {"page_size": page_size}, FIRST_SEARCH_PAGE, page_size
    )

    if total is not None and len(items) != total:
        raise IncompleteDiscoveryError(
            len(items), total, "treat a short workspace search as an absence"
        )
    return items


def as_workspace_item(item: dict) -> WorkspaceItem:
    return WorkspaceItem(
        id=item["id"],
        title=item.get("title"),
        type_id=item.get("type_id"),
        in_trash=item.get("in_trash") is True,
    )


# -----------------------------------------------------------------------------#
# ROUTE TWO — THE PROTOCOL LIST, BY FILTER
# -----------------------------------------------------------------------------#
def fetch_protocol_list(
    proto_list_url: str,
    headers: dict,
    page_size: int = 10,
    max_pull: int | None = None,
    **params,
) -> list[dict]:
    """This function does a first call through the API to get protocol IDs.
    We are only interested in IDs as a first pass since the protocol list
    does actually contain all the information we need to build verifiable
    protocol versions.
    """
    start_page = int(params.pop("page_id", FIRST_PAGE))
    params["page_size"] = page_size

    for attempt in (1, 2):
        protocols, total = fetch_pages(
            proto_list_url, headers, params, start_page, page_size, max_pull
        )
        # A capped or resumed pull is expected to be partial; nothing to verify.
        if total is None or max_pull is not None or start_page != FIRST_PAGE:
            break
        if len(protocols) == total:
            break
        if attempt == 2:
            raise IncompleteDiscoveryError(
                len(protocols), total, "write a partial pull"
            )
        print(
            f"Incomplete pull: got {len(protocols)} of {total} reported. Retrying once."
        )

    return [i["id"] for i in protocols]


# -----------------------------------------------------------------------------#
# SELECTION
# -----------------------------------------------------------------------------#
class SelectedProtocols(NamedTuple):
    selected: list[WorkspaceItem]
    trashed: list[WorkspaceItem]
    excluded: list[WorkspaceItem]
    warnings: list[str]


def select_protocols(items: Iterable[WorkspaceItem]) -> SelectedProtocols:
    """Gate whatever discovery found: not trashed, and actually a protocol.

    Trash is upstream's `in_trash` and nothing else — a protocol the user put in
    the trash is not tracked. Folder position decides nothing; the keyword route
    to retirement lives in lifecycle.
    """
    selected, trashed, excluded = [], [], []
    warnings: list[str] = []

    for item in items:
        if item.in_trash:
            trashed.append(item)
            continue
        if item.type_id is not None and item.type_id != PROTOCOL_TYPE_ID:
            warnings.append(
                f"skipping {item.id} ({item.title!r}): type_id {item.type_id} is "
                f"not a protocol"
            )
            excluded.append(item)
            continue

        selected.append(item)

    return SelectedProtocols(selected, trashed, excluded, warnings)


# -----------------------------------------------------------------------------#
# THE TWO DISCOVERY ROUTES
# -----------------------------------------------------------------------------#
def selection_detail(selection: SelectedProtocols) -> dict:
    return {
        "selected": len(selection.selected),
        "trashed": sorted(item.id for item in selection.trashed),
        "excluded": sorted(item.id for item in selection.excluded),
        "warnings": selection.warnings,
    }


def search_workspace(headers: dict, workspace_url: str) -> DiscoveredProtocols:
    """One sweep of the workspace search; the protocols in it carry their ids."""
    items = search_workspace_items(workspace_url, headers)
    protocols = [
        as_workspace_item(i)
        for i in items
        if i.get("content_type_id") == PROTOCOL_CONTENT_TYPE
    ]
    selection = select_protocols(protocols)
    detail = {
        "workspace_items": len(items),
        "workspace_protocols": len(protocols),
        **selection_detail(selection),
    }
    return DiscoveredProtocols(
        [item.id for item in selection.selected], "workspace", detail
    )


def search_by_filter(
    list_url: str, headers: dict, page_size: int = 10, max_pull: int | None = None
) -> DiscoveredProtocols:
    """Use get list method to list protocol ids under
    certain label (e.g 'shared_with_user)
    """
    params = {
        "filter": "shared_with_user",
        "key": " ",
        "order_field": "id",
        "fields": "id",
    }
    ids = fetch_protocol_list(
        list_url, headers, page_size=page_size, max_pull=max_pull, **params
    )
    return DiscoveredProtocols(ids, "filter", {"selected": len(ids), "degraded": True})


def discover(
    strategy: str,
    list_url: str,
    headers: dict,
    workspace_url: str = "",
    page_size: int = 10,
    max_pull: int | None = None,
) -> DiscoveredProtocols:
    if strategy == "workspace":
        if not workspace_url:
            raise ValueError(
                "PULL_STRATEGY=workspace needs WORKSPACE_ID — the workspace uri, "
                "the slug the browser shows for the workspace"
            )
        return search_workspace(headers, workspace_url)
    if strategy == "filter":
        return search_by_filter(list_url, headers, page_size, max_pull)
    raise ValueError(
        f"unknown PULL_STRATEGY {strategy!r}; expected 'workspace' or 'filter'"
    )
