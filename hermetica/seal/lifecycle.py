# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import difflib

# -----------------------------------------------------------------------------#
# LIFECYCLE TOKENS
# -----------------------------------------------------------------------------#
# Allowlist, matched exactly. The misspellings are aliased deliberately.
DEPRECATED_TOKENS: frozenset[str] = frozenset(
    {
        "deprecated",
        "depreciated",
        "depreceated",
        "deprecate",
    }
)

NEAR_MISS_RATIO = 0.8


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


def near_miss_tokens(keywords: str | None) -> list[str]:
    """Tokens that look like a lifecycle flag but are not one. Warning only."""

    def looks_like_one(token: str) -> bool:
        matches = difflib.get_close_matches(
            token, DEPRECATED_TOKENS, n=1, cutoff=NEAR_MISS_RATIO
        )
        return bool(matches)

    return [
        token
        for token in split_keywords(keywords)
        if token not in DEPRECATED_TOKENS and looks_like_one(token)
    ]
