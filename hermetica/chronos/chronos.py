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
from chronos.utils.filemanager_utils import Selection, select_protocols, walk_workspace
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

DB_OUT = os.getenv("DB", "db")

# `filter` is the older list-endpoint pull, kept as a fallback and incomplete
# by construction.
PULL_STRATEGY = os.getenv("PULL_STRATEGY", "walk")
DRY_RUN = os.getenv("DRY_RUN", "") not in ("", "0", "false", "False")

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
    "fields": "id",
}
PAGE_SIZE = 10
MAX_PULL = None  # no ceiling: pull every page so nothing is silently truncated


# -----------------------------------------------------------------------------#
# DISCOVERY
# -----------------------------------------------------------------------------#
class Discovery(NamedTuple):
    """Which protocol ids a pull will fetch, and how it decided."""

    ids: list[int]
    strategy: str
    detail: dict


def _selection_detail(selection: Selection) -> dict:
    return {
        "selected": len(selection.selected),
        "trashed": sorted(item.id for item in selection.trashed),
        "excluded": sorted(item.id for item in selection.excluded),
        "warnings": selection.warnings,
    }


def discover_by_walk(base_url: str, headers: dict) -> Discovery:
    """Walk the workspace and gate what it found. Touches no list endpoint."""
    items = walk_workspace(base_url, headers)
    selection = select_protocols(items)
    detail = {"workspace_items": len(items), **_selection_detail(selection)}
    return Discovery([item.id for item in selection.selected], "walk", detail)


def discover_by_filter(list_url: str, headers: dict) -> Discovery:
    """The fallback: one list call, no workspace walk, no trash filtering."""
    ids = fetch_protocol_list(
        list_url, headers, page_size=PAGE_SIZE, max_pull=MAX_PULL, **PULL_PARAMS
    )
    return Discovery(ids, "filter", {"selected": len(ids), "degraded": True})


def discover(strategy: str, base_url: str, list_url: str, headers: dict) -> Discovery:
    if strategy == "walk":
        return discover_by_walk(base_url, headers)
    if strategy == "filter":
        return discover_by_filter(list_url, headers)
    raise ValueError(f"unknown PULL_STRATEGY {strategy!r}; expected 'walk' or 'filter'")


# -----------------------------------------------------------------------------#
# LIFECYCLE FILTER
# -----------------------------------------------------------------------------#
class Screened(NamedTuple):
    kept: list[dict]
    deprecated: list[dict]
    warnings: list[str]


def screen_deprecated(protocols: list[dict]) -> Screened:
    """Drop anything the lab has tagged retired; warn on a near-miss spelling.

    Trash and this tag mean the same thing to the store — neither is sealed, and
    both close the open interval by being absent from the pull.
    """
    kept, deprecated, warnings = [], [], []
    for protocol in protocols:
        keywords = protocol.get("keywords")
        for token in near_miss_tokens(keywords):
            warnings.append(
                f"protocol {protocol.get('id')} carries keyword {token!r}, which "
                f"looks like a lifecycle flag but is not one — not acted on"
            )
        (deprecated if is_deprecated(keywords) else kept).append(protocol)
    return Screened(kept, deprecated, warnings)


# -----------------------------------------------------------------------------#
# ONE PULL
# -----------------------------------------------------------------------------#
def run_pull(db_name: str, pulled_at: int) -> dict:
    """Discover, fetch, screen, seal. Returns the entry written to the log."""
    # Stage one: decide what exists and what of it we want.
    discovery = discover(PULL_STRATEGY, BASE_URL, PROTOCOL_LIST_URL, HEADERS)
    print(f"strategy={discovery.strategy} -> {len(discovery.ids)} protocols to fetch")
    for warning in discovery.detail.get("warnings", []):
        print(f"  WARNING: {warning}")

    if DRY_RUN:
        return {"strategy": discovery.strategy, "dry_run": True, **discovery.detail}

    # Stage two: fetch each selected protocol. ratelimit/backoff live in the
    # request helper, so the by-ID storm stays inside the published limit.
    protocols = [fetch_protocol(p, PROTOCOL_URL, HEADERS) for p in discovery.ids]
    with open(f"{DB_OUT}/chronos_protocols.json", "w") as f:
        json.dump(protocols, f)

    screened = screen_deprecated(protocols)
    for warning in screened.warnings:
        print(f"  WARNING: {warning}")
    for protocol in screened.deprecated:
        print(f"  deprecated, not sealed: {protocol.get('id')} {protocol.get('title')}")

    # prepare cleaned dataclass of protocols
    artefacts = [build_protocol_artefact(p) for p in screened.kept]
    # format_entry hashes the exact bytes it stores, so blob and hash stay bound
    # to their row instead of to a list index.
    rows = format_entry(artefacts, pulled_at)
    diff = write_pull(db_name, rows, pulled_at)

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

    # One timestamp for the whole pull: every row opens its interval at the
    # instant the pull started, not at a per-row clock read.
    pulled_at = get_timestamp()

    try:
        entry = run_pull(db_name, pulled_at)
    except Exception as error:
        # A pull that dies leaves the store untouched, which is exactly the
        # state nobody notices. Record it and re-raise so cron exits non-zero.
        entry = {
            "strategy": PULL_STRATEGY,
            "failed": True,
            "error": f"{type(error).__name__}: {error}",
        }
        record_pull(DB_OUT, pulled_at, entry)
        failed_to = write_report(
            DB_OUT, format_failure({**entry, "pulled_at": pulled_at}, error)
        )
        print(f"pull FAILED — report -> {failed_to}")
        raise

    log = record_pull(DB_OUT, pulled_at, entry)
    report = write_report(DB_OUT, format_report({**entry, "pulled_at": pulled_at}))
    print(f"pull logged -> {log}")
    print(f"report       -> {report}")
    if EMAIL:
        # No mail route is configured yet; the report is written for a human to
        # collect or for cron to pipe onward.
        print(f"  (intended for {EMAIL} — no mail transport wired)")

    if DRY_RUN:
        print(json.dumps(entry, indent=2, sort_keys=True))
        raise SystemExit(0)

    ## now we run the second part of the chron job and that is to update
    ## the composed protocols
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
