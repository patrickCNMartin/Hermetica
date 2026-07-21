# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import argparse
import json
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


CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")

DB_OUT = os.getenv("DB","db")
# -----------------------------------------------------------------------------#
# DEFINE ILAB HEADERS
# -----------------------------------------------------------------------------#
HEADERS = {"Authorization": f"Bearer {API_KEY}"}
# -----------------------------------------------------------------------------#
# ENTRY
# -----------------------------------------------------------------------------#
if __name__ == "__main__":
    # Initialize data base and create if does not exist (schema only).
    db_name = f"{DB_OUT}/protocol_version_control.db"
    initialize_db(db_name)

    # Pull protocols from the API.
    protocols = get_protocol_list(BASE_URL, HEADERS)
    # Strip, hash protocols and return only unique protocols keyed by hash.
    processed_protocols = process_protocols(protocols)
    # Map to table rows and batch-insert (idempotent: existing hashes skipped).
    rows = to_rows(processed_protocols)
    n_new = insert_protocols(db_name, rows)
    print(f"Inserted {n_new} new protocol version(s).")

    # Optional JSON snapshot of the processed pull alongside the DB.
    # with open(f"{DB_OUT}/protocol_list.json", "w") as f:
    #     json.dump(processed_protocols, f)
