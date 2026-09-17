# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import backoff
import requests
from ratelimit import limits, sleep_and_retry

from sources.protocols_io.config import CALLS_PER_MINUTE


# -----------------------------------------------------------------------------#
# TRANSPORT
# -----------------------------------------------------------------------------#
# Everything that talks to protocols.io goes through call_api. Discovery lives
# in discover.py; this file is the wire and the by-ID read.
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
def call_api(url: str, headers: dict, params: dict | None = None) -> requests.Response:
    """Throttled, backoff-retried GET shared by every protocols.io call site."""
    response = requests.get(url=url, headers=headers, params=params)
    response.raise_for_status()
    return response


# -----------------------------------------------------------------------------#
# FETCH — ONE PROTOCOL, BY ID
# -----------------------------------------------------------------------------#
def fetch_protocol(protocol_id: int, protocol_url: str, headers: dict) -> dict:
    print(f"Processing Protocol: {protocol_id}")
    response = call_api(f"{protocol_url}{protocol_id}", headers)
    protocol = response.json()
    return protocol.get("payload", [])
