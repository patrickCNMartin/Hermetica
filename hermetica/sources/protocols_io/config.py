# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import re

# -----------------------------------------------------------------------------#
# SOURCE INFO
# -----------------------------------------------------------------------------#
SOURCE_NAME = "protocols_io"
RAW_DUMP_NAME = "protocols_io_raw.jsonl"
# -----------------------------------------------------------------------------#
# PROTOCOLS.IO READ
# -----------------------------------------------------------------------------#
# content_type_id says what kind of thing an item is; type_id sub-types a
# protocol (1 protocol, 3 collection, 4 document). Only real protocols are sealed.
PROTOCOL_CONTENT_TYPE = 1
PROTOCOL_TYPE_ID = 1

# The v4 workspace search: the whole folder tree, flat and paginated, in one
# sweep. Takes the workspace *uri* (the slug in the browser address bar), not a
# numeric id or a guid. 1-indexed, and its `next_page` is a URL rather than a
# page number.
WORKSPACE_SEARCH_PATH = "/v4/filemanager/workspaces/{workspace_id}/search"
FIRST_SEARCH_PAGE = 1
SEARCH_PAGE_SIZE = 100

# /v3/protocols is 0-indexed; the v4 search above is 1-indexed.
FIRST_PAGE = 0
# Backoff - max api calls per minute
CALLS_PER_MINUTE = 100

# -----------------------------------------------------------------------------#
# Response shape
# -----------------------------------------------------------------------------#

RICH_TEXT_FIELDS: tuple[str, ...] = (
    "description",
    "guidelines",
    "before_start",
    "materials_text",
    "disclaimer",
    "warning",
    "protocol_references",
)

UNIT_KEYS: tuple[str, ...] = ("unit", "temperatureUnit")

# `keywords` doubles as the lab's declaration channel. This prefix marks the one
# that names the executor: `executor:human`, `executor:biomek`.
EXECUTOR_PREFIX = "executor:"
# -----------------------------------------------------------------------------#
# SIGNED URL SCRUBBING
# -----------------------------------------------------------------------------#
VOLATILE_URL_PARAMS: tuple[str, ...] = (
    "X-Amz-Security-Token",
    "X-Amz-SignedHeaders",
    "X-Amz-Credential",
    "X-Amz-Algorithm",
    "X-Amz-Signature",
    "X-Amz-Expires",
    "X-Amz-Date",
    "Key-Pair-Id",
    "Signature",
    "Policy",
    "Expires",
)


SIGNED_PARAM = re.compile(
    r"([?&]|\\u0026)("
    + "|".join(re.escape(p) for p in VOLATILE_URL_PARAMS)
    + r")=[^&\"'\s\\]*",
    re.IGNORECASE,
)
