# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from collections.abc import Sequence
from time import sleep
import requests

from seal.contract import protocol_hash, select_protocol

# -----------------------------------------------------------------------------#
# CONSTANTS
# -----------------------------------------------------------------------------#
# Protocols.io follows a 0 index pages system
FIRST_PAGE = 0

# -----------------------------------------------------------------------------#
# ERROR HANDLING
# -----------------------------------------------------------------------------#
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


def get_protocol_ids(
    proto_list_url: str,
    headers: dict,
    page_size: int = 10,
    max_pull: int | None = None,
    **params,
) -> list[dict]:
    start_page = int(params.pop("page_id", FIRST_PAGE))
    params["page_size"] = page_size

    
    protocols, total = _walk_pages(proto_list_url,
                                   headers,
                                   params,
                                   start_page,
                                   page_size,
                                   max_pull)

    # A capped or resumed walk is expected to be partial; nothing to verify.
    if total is None or max_pull is not None or start_page != FIRST_PAGE:
        return [i["id"] for i in protocols]
    if len(protocols) == total:
        return [i["id"] for i in protocols]

    print(
        f"Incomplete pull: got {len(protocols)} of {total} reported. Retrying once."
    )
    protocols, total = _walk_pages(proto_list_url,
                                   headers,
                                   params,
                                   start_page,
                                   page_size,
                                   max_pull)
    
    if total is not None and len(protocols) != total:
        raise IncompletePullError(
            f"pulled {len(protocols)} protocols but the server reports "
            f"{total}; refusing to write a partial pull"
        )
    # Pull out ids
    return [i["id"] for i in protocols]

def get_protocol_list(protocol_ids: list,
    protocol_url : str,
    headers : dict
) -> list:
    # this feels a little bit hard coded but at the moment we do 
    # a little something stupid to avoid hit rate limits
    # we will pause so we don't hit the rate limit
    rate_limit = 90
    counter = 0
    protocol_list = []
    # prepare payload
    for p in protocol_ids:
        # simple prog check to see if this shit works
        if counter % 10 == 0:
            print(f"Processed {counter} protocols")
        response = requests.get(
                    url=f"{protocol_url}{p}", headers=headers)
        response.raise_for_status()
        payload = response.json()
        batch = payload.get("payload", [])
        counter += 1
        protocol_list.append(batch)
        
        # sleep to avoid rate limited
        if counter % rate_limit == 0:
            print("Waiting for rate limit to refresh...")
            sleep(60)   
    return protocol_list
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
