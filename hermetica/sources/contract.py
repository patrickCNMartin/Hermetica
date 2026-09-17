# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import re
from typing import Callable, NamedTuple

from seal.contract import ProtocolArtefact


# -----------------------------------------------------------------------------#
# WHAT AN ADAPTER HANDS BACK
# -----------------------------------------------------------------------------#
class DiscoveredProtocols(NamedTuple):
    ids: list[int]
    detail: dict


class FetchedProtocol(NamedTuple):
    """One protocol, read.

    `retired` means the source declares it out of use — never sealed, and no
    artefact is built for it. An artefact of None with retired False means the
    read itself failed; nothing may treat that as absence.
    """

    artefact: ProtocolArtefact | None
    retired: bool
    warnings: list[str]


class ProtocolSource(NamedTuple):
    """One platform, as two callables plus the name they were built for.

    The name travels with the callables because it is written to the store as
    part of identity — passed separately, it could disagree with them.
    """

    name: str
    discover: Callable[[], DiscoveredProtocols]
    fetch: Callable[[int], FetchedProtocol]


def check_source_name(name: str) -> str:
    """Reject a name that would make a protocol_uid ambiguous."""
    source_name = re.compile(r"^[a-z0-9_]+$")
    if not source_name.match(name or ""):
        raise ValueError(
            f"source name {name!r} must match {source_name.pattern} — it prefixes "
            f"every protocol_uid"
        )
    return name
