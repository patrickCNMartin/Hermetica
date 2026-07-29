# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import os
import json
from pathlib import Path

from dotenv import load_dotenv

# -----------------------------------------------------------------------------#
# IMPORT GENERIC UTILS
# -----------------------------------------------------------------------------#
from chronos.utils.request_utils import get_protocol_list, process_protocols,get_protocol_ids
from seal.dates import get_timestamp
from seal.store import initialize_db, format_entry, write_pull

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
    db_name = f"{DB_OUT}/chronos_dummy.db"
    initialize_db(db_name)

    # first we pull protocol ids from list
    ids = get_protocol_ids(
        PROTOCOL_LIST_URL,
        HEADERS,
        page_size=PAGE_SIZE,
        max_pull=MAX_PULL,
        **PULL_PARAMS,
    )
    
    protocols = get_protocol_list(
        ids,
        PROTOCOL_URL,
        HEADERS
    )
    with open(f"{DB_OUT}/chronos_protocol_list.json","w") as f:
           json.dump(protocols, f)
    # Strip, hash protocols and return only unique protocols keyed by hash.
    # processed_protocols = process_protocols(protocols)

    # # One pull, one timestamp: it dates both the new intervals and the closures.
    # pulled_at = get_timestamp()
    # rows = format_entry(processed_protocols, pulled_at)
    # diff = write_pull(db_name, rows, pulled_at)

    # print(
    #     f"new={len(diff['new'])} changed={len(diff['changed'])} "
    #     f"unchanged={len(diff['unchanged'])} deprecated={len(diff['absent'])}"
    # )
