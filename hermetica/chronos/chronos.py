# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import json
import os
from pathlib import Path
from typing import NamedTuple

from dotenv import load_dotenv

# -----------------------------------------------------------------------------#
# IMPORT GENERIC UTILS
# -----------------------------------------------------------------------------#
from chronos.utils.filemanager_utils import (
    SelectedProtocols,
    select_protocols,
    walk_workspace,
)
from chronos.utils.pull_log import record_pull
from chronos.utils.report import format_failure, format_report, write_report
from chronos.utils.request_utils import fetch_protocol, fetch_protocol_list
from compose.compose import active_protocols
from seal.contract import build_protocol_artefact
from seal.dates import get_timestamp
from seal.lifecycle import is_deprecated, near_miss_tokens
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

EMAIL = os.getenv("EMAIL", "")
LOGS = os.getenv("LOGS", "logs")
DB_OUT = os.getenv("DB", "db")

# Walk uses old entry point which goes through folder - works but archived
# filter uses the modern entry point - but sucks.
# This might need to be updated completely depending on what happens
# with protocols.io
PULL_STRATEGY = os.getenv("PULL_STRATEGY", "walk")


# -----------------------------------------------------------------------------#
# DEFINE ILAB HEADERS
# -----------------------------------------------------------------------------#
HEADERS = {"Authorization": f"Bearer {API_KEY}"}


# -----------------------------------------------------------------------------#
# ClASSES
# -----------------------------------------------------------------------------#
class DiscoveredProtocols(NamedTuple):
    ids: list[int]
    strategy: str  # this is only useful for the log
    detail: dict


class ScreenedProtocols(NamedTuple):
    kept: list[dict]
    deprecated: list[dict]
    warnings: list[str]


# -----------------------------------------------------------------------------#
# UTIL FUNCTIONS
# -----------------------------------------------------------------------------#
def _selection_detail(selection: SelectedProtocols) -> dict:
    return {
        "selected": len(selection.selected),
        "trashed": sorted(item.id for item in selection.trashed),
        "excluded": sorted(item.id for item in selection.excluded),
        "warnings": selection.warnings,
    }


def discover_by_walk(base_url: str, headers: dict) -> DiscoveredProtocols:
    """Use folder structure to find protocols by id."""
    items = walk_workspace(base_url, headers)
    selection = select_protocols(items)
    detail = {"workspace_items": len(items), **_selection_detail(selection)}
    return DiscoveredProtocols([item.id for item in selection.selected], "walk", detail)


def discover_by_filter(
    list_url: str, headers: dict, page_size: int = 10, max_pull: int | None = None
) -> DiscoveredProtocols:
    """Use get list method to list protocol ids under
    certain label (e.g 'shared_with_user)
    """
    params = {
        "filter": "shared_with_user",
        "key": " ",
        "order_field": "id",
        "peer_reviewed": 0,
        "fields": "id",
    }
    ids = fetch_protocol_list(
        list_url, headers, page_size=page_size, max_pull=max_pull, **params
    )
    return DiscoveredProtocols(ids, "filter", {"selected": len(ids), "degraded": True})


def discover(
    strategy: str,
    base_url: str,
    list_url: str,
    headers: dict,
    page_size: int = 10,
    max_pull: int | None = None,
) -> DiscoveredProtocols:
    if strategy == "walk":
        return discover_by_walk(base_url, headers)
    if strategy == "filter":
        return discover_by_filter(list_url, headers, page_size, max_pull)
    raise ValueError(f"unknown PULL_STRATEGY {strategy!r}; expected 'walk' or 'filter'")


def screen_deprecated(protocols: list[dict]) -> ScreenedProtocols:
    """
    Check if protocols has been deprecated.
    """
    kept, deprecated, warnings = [], [], []
    for protocol in protocols:
        keywords = protocol.get("keywords")
        for token in near_miss_tokens(keywords):
            warnings.append(
                f"protocol {protocol.get('id')} carries keyword {token!r}, which "
                f"looks like a lifecycle flag but is not one!"
                f"If you want to deprecate this protocol,"
                f"Please make sure you use one of the allowed terms"
            )
        (deprecated if is_deprecated(keywords) else kept).append(protocol)
    return ScreenedProtocols(kept, deprecated, warnings)


# -----------------------------------------------------------------------------#
# ONE PULL
# -----------------------------------------------------------------------------#
def run_pull(
    db_name: str,
    pulled_at: int,
    pull_strategy: str,
    base_url: str,
    protocol_list_url: str,
    protocol_url: str,
    headers: dict,
    db_out: str,
    page_size: int = 10,
    max_pull: int | None = None,
    dump_all: bool = True,
) -> dict:
    """Discover, fetch, screen, seal. Returns the entry written to the log."""

    # Get list of protocols for protocols.io
    # Either walk or use filter strategy
    discovery = discover(
        pull_strategy, base_url, protocol_list_url, headers, page_size, max_pull
    )
    print(f"strategy={discovery.strategy} -> {len(discovery.ids)} protocols to fetch")
    for warning in discovery.detail.get("warnings", []):
        print(f"  WARNING: {warning}")

    # Now that we have the list of availble protocols
    # we pull the protocols - the actual protocols with the relevant info
    protocols = [fetch_protocol(p, protocol_url, headers) for p in discovery.ids]
    # if dumpl_all is true we dump intermediate files.
    # This is more use for manual verification
    if dump_all:
        with open(f"{db_out}/chronos_protocols.json", "w") as f:
            json.dump(protocols, f)

    # Check if protocols have been deprecated
    # NOTE: Trashed protocols can only be found when using walk
    # and they are filtered out at that time.
    # This is section is specifically for protcols that are placed
    # in folder such as 'old' and contain the 'deprecated' keyword.
    # If the core doesn't add that key word AND it is in OLD
    # it will come up as active.
    screened = screen_deprecated(protocols)
    for warning in screened.warnings:
        print(f"  WARNING: {warning}")
    for protocol in screened.deprecated:
        print(f"  deprecated, not sealed: {protocol.get('id')} {protocol.get('title')}")

    # Build a protocol artefact/datatype which allows for some addition validation
    # Also contains everything we need in the correct format
    artefacts = [build_protocol_artefact(p) for p in screened.kept]
    # reformat to something that can inserted into a data base
    rows = format_entry(artefacts, pulled_at)
    # This will
    diff = write_pull(db_name, rows, pulled_at)

    # Return some more info about the whole thing
    # assumes that the writing to db went well otherwise it would
    # have raised an error.
    return {
        "strategy": discovery.strategy,
        **discovery.detail,
        "fetched": len(protocols),
        "deprecated": sorted(p.get("id") for p in screened.deprecated),
        "sealed": len(rows),
        "diff": {key: sorted(value) for key, value in diff.items()},
        "warnings": discovery.detail.get("warnings", []) + screened.warnings,
    }


# -----------------------------------------------------------------------------#
# ENTRY
# -----------------------------------------------------------------------------#
if __name__ == "__main__":
    # Initialize data base and create if does not exist (schema only).
    db_name = f"{DB_OUT}/chronos.db"
    initialize_db(db_name)

    # pull time stamp
    # This is when we start the pull - everything else follows this pull time
    pulled_at = get_timestamp()
    # Testing if we can pull from the protocols.io database
    try:
        entry = run_pull(
            db_name,
            pulled_at,
            PULL_STRATEGY,
            BASE_URL,
            PROTOCOL_LIST_URL,
            PROTOCOL_URL,
            HEADERS,
            DB_OUT,
            page_size=10,
            max_pull=None,
            dump_all=True,
        )
    except Exception as error:
        # If the pull failed, the error is parsed to the log
        entry = {
            "strategy": PULL_STRATEGY,
            "failed": True,
            "error": f"{type(error).__name__}: {error}",
        }
        record_pull(LOGS, pulled_at, entry)
        failure = format_failure({**entry, "pulled_at": pulled_at}, error)
        write_report(LOGS, failure)
        print("pull FAILED — Check pull logs")
        raise

    # finally we write the pull logs
    log = record_pull(LOGS, pulled_at, entry)
    log_entry = format_report({**entry, "pulled_at": pulled_at})
    report = write_report(LOGS, log_entry)

    # place holder to transfer log to the maintainer
    if EMAIL:
        # No mail route is configured yet; the report is written for a human to
        # collect or for cron to pipe onward.
        print(f"  (intended for {EMAIL} — no mail transport wired)")

    # This is section is here for testing purpose on the live db
    # This won't be part of the cron job.
    protocol_names = active_protocols(db_name)
    with open(f"{DB_OUT}/fixture_names.json", "w") as f:
        json.dump(protocol_names, f)

    # exporting locks and testing to see things
    wanted = [h for h, title in protocol_names.items() if "SDS lysis" in title]
    from seal.seal import export_lock, export_pins, generate_protocol_lock

    lock = generate_protocol_lock(wanted, db=db_name)
    export_pins(lock, f"{DB_OUT}/pins.lock")
    export_lock(lock, f"{DB_OUT}/lock.lock")
    from scribe.markdown import export_markdown, to_markdown

    md = to_markdown(lock, db_name)
    export_markdown(lock, f"{DB_OUT}/protocol_render_template_from_db.md", db_name)
    export_markdown(lock, f"{DB_OUT}/protocol_render_template_from_lock.md")
