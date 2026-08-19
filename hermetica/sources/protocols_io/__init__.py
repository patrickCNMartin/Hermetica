# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import json
from pathlib import Path

from sources.contract import (
    DiscoveredProtocols,
    FetchedProtocol,
    ProtocolSource,
    check_source_name,
)
from sources.protocols_io.artefact import build_protocol_artefact
from sources.protocols_io.client import fetch_protocol
from sources.protocols_io.discover import discover
from sources.protocols_io.lifecycle import screen_protocol

# -----------------------------------------------------------------------------#
# CONSTANTS & STORES
# -----------------------------------------------------------------------------#
SOURCE_NAME = "protocols_io"

# One raw record per line, appended as it arrives: a pull that dies halfway
# leaves what it read rather than nothing, and a torn write costs one record.
RAW_DUMP_NAME = "protocols_io_raw.jsonl"


# -----------------------------------------------------------------------------#
# BUILD THE SOURCE
# -----------------------------------------------------------------------------#
def build_source(
    base_url: str,
    api_key: str,
    strategy: str = "walk",
    list_url: str = "",
    protocol_url: str = "",
    page_size: int = 10,
    max_pull: int | None = None,
    raw_dump: str = "",
) -> ProtocolSource:
    """Close over this workspace's config and hand back discover + fetch.

    Nothing below this reads the environment; every URL and key arrives here.
    """
    check_source_name(SOURCE_NAME)
    headers = {"Authorization": f"Bearer {api_key}"}
    list_url = list_url or f"{base_url}/v3/protocols"
    protocol_url = protocol_url or f"{base_url}/v4/protocols/"

    dump_path = Path(raw_dump) / RAW_DUMP_NAME if raw_dump else None
    if dump_path is not None:
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        # Truncated once per pull, when the source is built.
        dump_path.write_text("", encoding="utf-8")

    def _discover() -> DiscoveredProtocols:
        return discover(strategy, base_url, list_url, headers, page_size, max_pull)

    def _fetch(protocol_id: int) -> FetchedProtocol:
        record = fetch_protocol(protocol_id, protocol_url, headers)
        if dump_path is not None:
            with dump_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")

        screened = screen_protocol(record)
        if screened.retired:
            return FetchedProtocol(None, True, screened.warnings)
        return FetchedProtocol(
            build_protocol_artefact(record), False, screened.warnings
        )

    return ProtocolSource(SOURCE_NAME, _discover, _fetch)
