# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from typing import NamedTuple

from seal.lifecycle import is_deprecated, near_miss_tokens


# -----------------------------------------------------------------------------#
# ClASSES
# -----------------------------------------------------------------------------#
class ScreenedProtocol(NamedTuple):
    retired: bool
    warnings: list[str]


# -----------------------------------------------------------------------------#
# SCREENING
# -----------------------------------------------------------------------------#
def screen_protocol(protocol: dict) -> ScreenedProtocol:
    """Does the lab declare this protocol out of use?

    Trash is handled during discovery — this is the other trigger, a token in
    `keywords`. A spelling close to a listed one warns and stays live.
    """
    keywords = protocol.get("keywords")
    warnings = [
        f"protocol {protocol.get('id')} carries keyword {token!r}, which "
        f"looks like a lifecycle flag but is not one!"
        f"If you want to deprecate this protocol,"
        f"Please make sure you use one of the allowed terms"
        for token in near_miss_tokens(keywords)
    ]
    return ScreenedProtocol(is_deprecated(keywords), warnings)
