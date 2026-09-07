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
from sources.protocols_io.config import (
    RAW_DUMP_NAME,
    SOURCE_NAME,
    WORKSPACE_SEARCH_PATH,
)
from sources.protocols_io.discover import discover
from sources.protocols_io.lifecycle import screen_protocol


# -----------------------------------------------------------------------------#
# TRANSLATION LAYER - BUILDING SOURCE FUNCTION
# -----------------------------------------------------------------------------#
# This is constructor function that is being called by chronos
# allows us to swap the source without changing anything else
# And using functions... Yes Yes I know could have use objects and methods
# But... functional make more sense than OOP for me.
def build_source(
    base_url: str,
    api_key: str,
    strategy: str = "workspace",
    workspace_id: str = "",
    list_url: str = "",
    protocol_url: str = "",
    page_size: int = 10,
    max_pull: int | None = None,
    raw_dump: str = "",
) -> ProtocolSource:
    check_source_name(SOURCE_NAME)
    headers = {"Authorization": f"Bearer {api_key}"}
    list_url = list_url or f"{base_url}/v3/protocols"
    protocol_url = protocol_url or f"{base_url}/v4/protocols/"
    # The workspace *uri* — the slug in the browser address bar, not a guid.
    workspace_url = (
        base_url + WORKSPACE_SEARCH_PATH.format(workspace_id=workspace_id)
        if workspace_id
        else ""
    )

    dump_path = Path(raw_dump) / RAW_DUMP_NAME if raw_dump else None
    if dump_path is not None:
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        # Truncated once per pull, when the source is built.
        dump_path.write_text("", encoding="utf-8")

    # Returns a callable to parse to ProtocolSource
    # This essentially holds the source specific functions
    # but using a common structure.
    # It all boils down to having a common interface layer for any new tool
    # make me wonder if there is a even more common interface.
    def protocol_discover() -> DiscoveredProtocols:
        return discover(strategy, list_url, headers, workspace_url, page_size, max_pull)

    def protocol_fetch(protocol_id: int) -> FetchedProtocol:
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

    return ProtocolSource(SOURCE_NAME, protocol_discover, protocol_fetch)
