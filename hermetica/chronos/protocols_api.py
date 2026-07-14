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
from hermetica.chronos.utils.request_utils import get_protocol_list

# -----------------------------------------------------------------------------#
# SET ENV VARS
# -----------------------------------------------------------------------------#
dotenv_path = Path.cwd() / "env" / ".env"
load_dotenv(dotenv_path=dotenv_path)


API_KEY = os.getenv("API_KEY", "")
BASE_URL = os.getenv("BASE_URL", "")


CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")


# -----------------------------------------------------------------------------#
# DEFINE ILAB HEADERS
# -----------------------------------------------------------------------------#
HEADERS = {"Authorization": f"Bearer {API_KEY}"}


# -----------------------------------------------------------------------------#
# DEFINE ARGUMENTS
# -----------------------------------------------------------------------------#
def parse_args():
    parser = argparse.ArgumentParser(
        description="Protocols.io API client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python protocols_api.py --outfile file_out.md
        """,
    )
    parser.add_argument(
        "--tell_me",
        required=False,
        type=str,
        help="Pleace holder for the future",
    )

    return parser.parse_args()


# -----------------------------------------------------------------------------#
# ENTRY
# -----------------------------------------------------------------------------#
if __name__ == "__main__":
    args = parse_args()
    # import pdb;pdb.set_trace()
    protocols = get_protocol_list(BASE_URL, HEADERS)
    with open("protocol_list.json", "w") as f:
        json.dump(protocols, f)
