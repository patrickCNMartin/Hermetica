# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from typing import Iterable, NamedTuple

from sources.contract import DiscoveredProtocols
from sources.protocols_io.client import call_api
from sources.protocols_io.config import (
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
        super().__init__(f"read {read} of {reported} reported; refusing to {refusing}")


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
) -> tuple[list[dict], int | None]:
    """Fetch pages until the server says stop. Returns (items, total_results)."""
    items: list[dict] = []
    total: int | None = None
    page = start_page

    while True:
        print(f"Processing Page: {page}")
        # v4 nests the answer under `payload`; a body without one is not a page.
        payload = call_api(url, headers, {**params, "page_id": page}).json()["payload"]
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
# THE WORKSPACE SEARCH
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
# DISCOVERY
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
    return DiscoveredProtocols([item.id for item in selection.selected], detail)
