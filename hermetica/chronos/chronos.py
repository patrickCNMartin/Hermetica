# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import os
from pathlib import Path

from dotenv import load_dotenv

# -----------------------------------------------------------------------------#
# IMPORT GENERIC UTILS
# -----------------------------------------------------------------------------#
from chronos.utils.request_utils import (get_protocol_list,process_protocols)
from chronos.utils.db import (initialize_db, to_rows, insert_protocols)

# -----------------------------------------------------------------------------#
# SET ENV VARS
# -----------------------------------------------------------------------------#
dotenv_path = Path.cwd() / "env" / ".env"
load_dotenv(dotenv_path=dotenv_path)


API_KEY = os.getenv("API_KEY", "")
BASE_URL = os.getenv("BASE_URL", "")
PROTOCOL_URL = os.getenv("PROTOCOL_URL", f"{BASE_URL}/v3/protocols")


CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")

DB_OUT = os.getenv("DB","db")
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
}
PAGE_SIZE = 10
MAX_PULL = None  # no ceiling: pull every page so nothing is silently truncated
# -----------------------------------------------------------------------------#
# ENTRY
# -----------------------------------------------------------------------------#
if __name__ == "__main__":
    # Initialize data base and create if does not exist (schema only).
    db_name = f"{DB_OUT}/protocol_version_control.db"
    initialize_db(db_name)

    # Pull protocols from the API.
    protocols = get_protocol_list(
        PROTOCOL_URL,
        HEADERS,
        page_size=PAGE_SIZE,
        max_pull=MAX_PULL,
        **PULL_PARAMS,
    )
    # Strip, hash protocols and return only unique protocols keyed by hash.
    processed_protocols = process_protocols(protocols)
    # Map to table rows and batch-insert (idempotent: existing hashes skipped).
    rows = to_rows(processed_protocols)
    n_new = insert_protocols(db_name, rows)
    print(f"Inserted {n_new} new protocol version(s).")

    # Optional JSON snapshot of the processed pull alongside the DB.
    # with open(f"{DB_OUT}/protocol_list.json", "w") as f:
    #     json.dump(processed_protocols, f)
