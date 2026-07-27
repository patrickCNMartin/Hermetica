# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from collections.abc import Sequence

import requests

from seal.contract import protocol_hash, select_protocol

# protocols.io numbers pages from zero: page_id=0 returns pagination.current_page 1.
# Starting at 1 silently skips the first page.
FIRST_PAGE = 0


class IncompletePullError(RuntimeError):
    """A pull collected fewer records than the server reported available."""


# -----------------------------------------------------------------------------#
# PULL
# -----------------------------------------------------------------------------#
def _walk_pages(
    protocol_url: str,
    headers: dict,
    params: dict,
    start_page: int,
    page_size: int,
    max_pull: int | None,
) -> tuple[list[dict], int | None]:
    """Fetch pages until the server says stop. Returns (items, total_results).

    Prefers the response's own `pagination.next_page`; falls back to the
    empty/short-page heuristic for endpoints that publish no pagination block.
    """
    items: list[dict] = []
    total: int | None = None
    page = start_page

    while max_pull is None or (page - start_page) < max_pull:
        print(f"Processing Page: {page}")
        response = requests.get(
            url=protocol_url, headers=headers, params={**params, "page_id": page}
        )
        response.raise_for_status()
        payload = response.json()
        batch = payload.get("items", [])
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


def get_protocol_list(
    protocol_url: str,
    headers: dict,
    page_size: int = 10,
    max_pull: int | None = None,
    **params,
) -> list[dict]:
    """Walk a paginated protocol endpoint until it runs out.

    Caller supplies the URL and query params. `max_pull=None` reads every page;
    pass an int to cap it.

    When the server reports `total_results` and the walk was not deliberately
    capped, the collected count is checked against it. A mismatch retries the
    whole pull once, then raises — an incomplete pull must never be mistaken for
    protocols having been deleted upstream.
    """
    start_page = int(params.pop("page_id", FIRST_PAGE))
    params["page_size"] = page_size

    walk = (protocol_url, headers, params, start_page, page_size, max_pull)
    protocols, total = _walk_pages(*walk)

    # A capped or resumed walk is expected to be partial; nothing to verify.
    if total is None or max_pull is not None or start_page != FIRST_PAGE:
        return protocols
    if len(protocols) == total:
        return protocols

    print(
        f"Incomplete pull: got {len(protocols)} of {total} reported. Retrying once."
    )
    protocols, total = _walk_pages(*walk)
    if total is not None and len(protocols) != total:
        raise IncompletePullError(
            f"pulled {len(protocols)} protocols but the server reports "
            f"{total}; refusing to write a partial pull"
        )
    return protocols


# -----------------------------------------------------------------------------#
# SELECT / HASH / DEDUPE
# -----------------------------------------------------------------------------#
def process_protocols(
    protocols: list,
    include_fields: Sequence[str] | None = None,
    metadata_fields: Sequence[str] | None = None,
) -> dict:
    """Apply the content contract, hash, and collapse duplicates by hash."""
    selected = [
        select_protocol(p, include_fields, metadata_fields) for p in protocols
    ]
    blobbed = [protocol_hash(p, include_fields) for p in selected]
    return get_unique_protocols(selected, blobbed)


def get_unique_protocols(selected_protocols: list, blobbed_protocols: list) -> dict:
    unique = {}
    for p, h in zip(selected_protocols, blobbed_protocols):
        if h not in unique:
            unique[h] = p
    return unique
