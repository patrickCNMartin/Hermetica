# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import backoff
import requests
from ratelimit import limits, sleep_and_retry

# -----------------------------------------------------------------------------#
# CONSTANTS & STORES
# -----------------------------------------------------------------------------#
# Protocols.io follows a 0 index pages system
FIRST_PAGE = 0
# Unconfirmed placeholder — verify against protocols.io docs/response headers.
CALLS_PER_MINUTE = 100


# -----------------------------------------------------------------------------#
# ERROR HANDLING
# -----------------------------------------------------------------------------#
class IncompletePullError(RuntimeError):
    """A pull collected fewer records than the server reported available."""


# -----------------------------------------------------------------------------#
# PULL
# -----------------------------------------------------------------------------#
@sleep_and_retry
@limits(calls=CALLS_PER_MINUTE, period=60)
@backoff.on_exception(
    backoff.expo,
    requests.exceptions.HTTPError,
    max_tries=5,
    giveup=lambda e: (
        e.response is not None
        and e.response.status_code < 500
        and e.response.status_code != 429
    ),
)
def _call_api(url: str, headers: dict, params: dict | None = None) -> requests.Response:
    """Throttled, backoff-retried GET shared by every protocols.io call site."""
    response = requests.get(url=url, headers=headers, params=params)
    response.raise_for_status()
    return response


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
        response = _call_api(protocol_url, headers, {**params, "page_id": page})
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

    protocols, total = _walk_pages(
        proto_list_url, headers, params, start_page, page_size, max_pull
    )
    # A capped or resumed walk is expected to be partial; nothing to verify.
    if total is None or max_pull is not None or start_page != FIRST_PAGE:
        return [i["id"] for i in protocols]
    if len(protocols) == total:
        return [i["id"] for i in protocols]

    print(f"Incomplete pull: got {len(protocols)} of {total} reported. Retrying once.")
    protocols, total = _walk_pages(
        proto_list_url, headers, params, start_page, page_size, max_pull
    )

    if total is not None and len(protocols) != total:
        raise IncompletePullError(
            f"pulled {len(protocols)} protocols but the server reports "
            f"{total}; refusing to write a partial pull"
        )
    # Pull out protocol ids

    return [i["id"] for i in protocols]


def fetch_protocol(protocol_id: int, protocol_url: str, headers: dict) -> dict:
    print(f"Processing Protocol: {protocol_id}")
    response = _call_api(f"{protocol_url}{protocol_id}", headers)
    protocol = response.json()
    return protocol.get("payload", [])
