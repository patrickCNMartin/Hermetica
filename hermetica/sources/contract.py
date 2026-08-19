# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import re
from typing import Callable, NamedTuple

from seal.contract import ProtocolArtefact

# -----------------------------------------------------------------------------#
# CONSTANTS & STORES
# -----------------------------------------------------------------------------#
# The source name prefixes every protocol_uid, so a name carrying the separator
# would make the uid ambiguous.
SOURCE_NAME = re.compile(r"^[a-z0-9_]+$")


# -----------------------------------------------------------------------------#
# ERROR HANDLING
# -----------------------------------------------------------------------------#
class UnreadableProtocolError(RuntimeError):
    """A source returned neither an artefact nor a retirement."""


# -----------------------------------------------------------------------------#
# WHAT AN ADAPTER HANDS BACK
# -----------------------------------------------------------------------------#
class DiscoveredProtocols(NamedTuple):
    ids: list[int]
    strategy: str  # this is only useful for the log
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
    if not SOURCE_NAME.match(name or ""):
        raise ValueError(
            f"source name {name!r} must match {SOURCE_NAME.pattern} — it prefixes "
            f"every protocol_uid"
        )
    return name
