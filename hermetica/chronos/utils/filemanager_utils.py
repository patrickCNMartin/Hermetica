# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from typing import Iterable, NamedTuple

from chronos.utils.request_utils import _call_api

# -----------------------------------------------------------------------------#
# CONSTANTS & STORES
# -----------------------------------------------------------------------------#
# content_type_id says what kind of thing an item is; type_id sub-types a
# protocol (1 protocol, 3 collection, 4 document). Only real protocols are sealed.
FOLDER_CONTENT_TYPE = 10
PROTOCOL_CONTENT_TYPE = 1
PROTOCOL_TYPE_ID = 1

# /v3/folders/<guid>/ids is 1-indexed. /v3/protocols is 0-indexed. Asking this
# one for page 0 returns an empty `ids` array *with* a populated `next_page`, so
# a pager written against the other endpoint finds nothing and exits cleanly.
FIRST_FOLDER_PAGE = 1
FOLDER_PAGE_SIZE = 100
# /v3/filemanager/items takes repeated ids[] params; batched to keep URLs sane.
ITEM_BATCH = 50

# The workspace Trash is a top-level folder and carries `in_trash: False` itself —
# it is the container, not a trashed item. Its own name is the only way in.
TRASH_FOLDER_TITLE = "trash"


# -----------------------------------------------------------------------------#
# ERROR HANDLING
# -----------------------------------------------------------------------------#
class IncompleteWalkError(RuntimeError):
    """A folder yielded fewer item ids than the server reported it holds."""


# -----------------------------------------------------------------------------#
# WALK ITEMS
# -----------------------------------------------------------------------------#
class WalkItem(NamedTuple):
    """One protocol found in the workspace tree, with where it was found.

    Discovery yields ids; the by-ID fetch is the only source of content. Only
    what gates an id or names it in a warning is kept.
    `in_trash` is the upstream flag; `trashed` also counts a trashed branch.
    """

    id: int
    title: str
    type_id: int | None
    in_trash: bool
    trashed: bool
    path: str

    @property
    def flag_disagrees(self) -> bool:
        return self.in_trash != self.trashed


# -----------------------------------------------------------------------------#
# THE WALK
# -----------------------------------------------------------------------------#
def fetch_top_folders(base_url: str, headers: dict) -> list[dict]:
    """The workspace root folders."""
    url = f"{base_url}/v3/filemanager/folders"
    return _call_api(url, headers, {"top": 1}).json().get("folders") or []


def fetch_folder_ids(
    base_url: str, headers: dict, guid: str, page_size: int = FOLDER_PAGE_SIZE
) -> list[int]:
    """Every item id in one folder. The per-folder total is the only count check."""
    ids: list[int] = []
    total: int | None = None
    page = FIRST_FOLDER_PAGE

    while True:
        payload = _call_api(
            f"{base_url}/v3/folders/{guid}/ids",
            headers,
            {"page_size": page_size, "page_id": page},
        ).json()
        ids.extend(payload.get("ids") or [])

        pagination = payload.get("pagination") or {}
        if total is None:
            total = pagination.get("total_results")
        if not pagination.get("next_page"):
            break
        page += 1

    if total is not None and len(ids) != total:
        raise IncompleteWalkError(
            f"folder {guid} yielded {len(ids)} item ids but the server reports "
            f"{total}; refusing to treat a short read as an empty folder"
        )
    return ids


def fetch_items(
    base_url: str, headers: dict, item_ids: Iterable[int], batch: int = ITEM_BATCH
) -> list[dict]:
    """Resolve file manager item ids to their full items."""
    item_ids = list(item_ids)
    items: list[dict] = []
    for start in range(0, len(item_ids), batch):
        chunk = item_ids[start : start + batch]
        payload = _call_api(
            f"{base_url}/v3/filemanager/items",
            headers,
            [("ids[]", i) for i in chunk] + [("page_size", len(chunk))],
        ).json()
        items.extend(payload.get("items") or [])
    return items


def _is_trash_folder(folder: dict) -> bool:
    """The workspace Trash, by name — it reports in_trash False, parent_guid None."""
    return str(folder.get("title") or "").strip().casefold() == TRASH_FOLDER_TITLE


def _as_walk_item(item: dict, path: str, trashed_branch: bool) -> WalkItem:
    in_trash = item.get("in_trash") is True
    return WalkItem(
        id=item["id"],
        title=item.get("title"),
        type_id=item.get("type_id"),
        in_trash=in_trash,
        trashed=in_trash or trashed_branch,
        path=path,
    )


def walk_workspace(
    base_url: str, headers: dict, page_size: int = FOLDER_PAGE_SIZE
) -> list[WalkItem]:
    """Every protocol in the workspace, folder by folder. Uses [Archived] endpoints."""
    queue: list[tuple[str, str, bool]] = [
        (folder["guid"], str(folder.get("title") or ""), _is_trash_folder(folder))
        for folder in fetch_top_folders(base_url, headers)
    ]

    found: dict[int, WalkItem] = {}
    walked: set[str] = set()

    while queue:
        guid, path, trashed_branch = queue.pop(0)
        # A folder reachable by two routes would otherwise be walked twice, and a
        # cycle would never terminate.
        if guid in walked:
            continue
        walked.add(guid)

        for item in fetch_items(
            base_url, headers, fetch_folder_ids(base_url, headers, guid, page_size)
        ):
            content_type = item.get("content_type_id")
            child_path = f"{path}/{item.get('title')}"
            if content_type == FOLDER_CONTENT_TYPE:
                inherited = (
                    trashed_branch
                    or item.get("in_trash") is True
                    or _is_trash_folder(item)
                )
                queue.append((item["guid"], child_path, inherited))
            elif content_type == PROTOCOL_CONTENT_TYPE:
                # Filed in two folders costs one by-ID fetch, not two.
                found.setdefault(
                    item["id"], _as_walk_item(item, path, bool(trashed_branch))
                )

    return sorted(found.values(), key=lambda i: i.id)


# -----------------------------------------------------------------------------#
# SELECTION
# -----------------------------------------------------------------------------#
class SelectedProtocols(NamedTuple):
    selected: list[WalkItem]
    trashed: list[WalkItem]
    excluded: list[WalkItem]
    warnings: list[str]


def select_protocols(items: Iterable[WalkItem]) -> SelectedProtocols:
    """Gate whatever discovery found: not trashed, and actually a protocol.

    How an item was discovered does not qualify or disqualify it — trash and
    type are the only two states the API states for itself.
    """
    selected, trashed, excluded = [], [], []
    warnings: list[str] = []

    for item in items:
        if item.flag_disagrees:
            warnings.append(
                f"in_trash flag disagrees with folder position for {item.id} "
                f"({item.title!r} at {item.path!r})"
            )
        if item.trashed:
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
