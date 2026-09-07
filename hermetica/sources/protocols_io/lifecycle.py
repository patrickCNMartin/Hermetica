# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import difflib
from typing import NamedTuple


# -----------------------------------------------------------------------------#
# ClASSES
# -----------------------------------------------------------------------------#
class ScreenedProtocol(NamedTuple):
    retired: bool
    warnings: list[str]


# -----------------------------------------------------------------------------#
# LIFECYCLE TOKENS
# -----------------------------------------------------------------------------#
# This stays here because only ever used here.
# Might also remove the near miss part
DEPRECATED_TOKENS: frozenset[str] = frozenset(
    {
        "deprecated",
        "depreciated",
        "depreceated",
        "deprecate",
    }
)


# -----------------------------------------------------------------------------#
# TOKENS
# -----------------------------------------------------------------------------#
def split_keywords(keywords: str | None) -> list[str]:
    """`keywords` is flat comma-separated text — split, trim, casefold."""
    if not keywords:
        return []
    return [token.strip().casefold() for token in keywords.split(",") if token.strip()]


def is_deprecated(keywords: str | None) -> bool:
    """True when the lab has declared this protocol retired."""
    return any(token in DEPRECATED_TOKENS for token in split_keywords(keywords))


# Not sure if I want to keep this
# this is more of a guard againts my own incomptence in spelling...
def near_miss_tokens(keywords: str | None, near_miss_ratio: float = 0.8) -> list[str]:
    """Tokens that look like a lifecycle flag but are not one. Warning only."""

    def looks_like_one(token: str) -> bool:
        matches = difflib.get_close_matches(
            token, DEPRECATED_TOKENS, n=1, cutoff=near_miss_ratio
        )
        return bool(matches)

    return [
        token
        for token in split_keywords(keywords)
        if token not in DEPRECATED_TOKENS and looks_like_one(token)
    ]


# -----------------------------------------------------------------------------#
# SCREENING
# -----------------------------------------------------------------------------#
# Protocol screening only really applies to protocols.io at the moment.
# We cannot ensure that "keywords" will be present in another plateform.
# We still want to have a mechanism that can check for deprecated protocols
# but the previous engineering is based to much on protocols. io structure.
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
