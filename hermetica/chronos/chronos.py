# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import json
import os
from pathlib import Path

from dotenv import load_dotenv

# -----------------------------------------------------------------------------#
# IMPORT GENERIC UTILS
# -----------------------------------------------------------------------------#
from chronos.utils.request_utils import fetch_protocol, fetch_protocol_list
from seal.contract import build_protocol_artefact
from seal.dates import get_timestamp
from seal.store import format_entry, initialize_db, write_pull

# -----------------------------------------------------------------------------#
# SET ENV VARS
# -----------------------------------------------------------------------------#
dotenv_path = Path.cwd() / "env" / ".env"
load_dotenv(dotenv_path=dotenv_path)


API_KEY = os.getenv("API_KEY", "")
BASE_URL = os.getenv("BASE_URL", "")
PROTOCOL_LIST_URL = os.getenv("PROTOCOL_LIST_URL", f"{BASE_URL}/v3/protocols")
PROTOCOL_URL = os.getenv("PROTOCOL_URL", f"{BASE_URL}/v4/protocols/")

CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")

DB_OUT = os.getenv("DB", "db")
# -----------------------------------------------------------------------------#
# DEFINE ILAB HEADERS
# -----------------------------------------------------------------------------#
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

# -----------------------------------------------------------------------------#
# PULL SCOPE
# -----------------------------------------------------------------------------#
# order_field MUST be a unique key. Sorting by `date` (or `name`) lets the
# server's page window shift between requests, so pages overlap and later
# protocols are never reached — a measured 51 -> 29 loss. `key` is required by
# the API; an empty value returns 400.
PULL_PARAMS = {
    "filter": "shared_with_user",
    "key": " ",
    "order_field": "id",
    "peer_reviewed": 0,
    "fields" : "id"
}
PAGE_SIZE = 10
MAX_PULL = None  # no ceiling: pull every page so nothing is silently truncated
# -----------------------------------------------------------------------------#
# ENTRY
# -----------------------------------------------------------------------------#
if __name__ == "__main__":
    # Initialize data base and create if does not exist (schema only).
    db_name = f"{DB_OUT}/chronos_database.db"
    initialize_db(db_name)

    # One timestamp for the whole pull: every row opens its interval at the
    # instant the pull started, not at a per-row clock read.
    pulled_at = get_timestamp()

    # first we pull protocol ids from list
    ids = fetch_protocol_list(
        PROTOCOL_LIST_URL,
        HEADERS,
        page_size=PAGE_SIZE,
        max_pull=MAX_PULL,
        **PULL_PARAMS,
    )
    # Next we process each id to pull the actual protocol
    # Note that to avoid hitting API rate limit, we added 
    # ratelimit/backoff decorators to the api call function
    protocols = [fetch_protocol(p,PROTOCOL_URL, HEADERS) for p in ids]
    with open(f"{DB_OUT}/chronos_protocols.json", "w") as f:
        json.dump(protocols,f)
    # prepare cleaned dataclass of protocols
    protocols = [build_protocol_artefact(p) for p in protocols]

    # format_entry hashes the exact bytes it stores, so blob and hash stay bound
    # to their row instead of to a list index.
    rows = format_entry(protocols, pulled_at)
    diff = write_pull(db_name, rows, pulled_at)
    for state, affected in diff.items():
        print(f"{state}: {len(affected)}")