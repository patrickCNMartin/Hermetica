# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import re
import hashlib
import json
import unicodedata
from typing import Any
from collections.abc import Sequence

from seal.canonical import canonical_json, hash_bytes

# -----------------------------------------------------------------------------#
# CONTENT CONTRACT
# -----------------------------------------------------------------------------#
# The only fields hashed. Changing this re-hashes every protocol version.
STABLE_FIELDS: tuple[str, ...] = (
    "id",
    "guid",
    "title",
    "description",
    "doi",
    "uri",
    "guidelines",
    "materials",
    "materials_text",
    "units",
    "warning",
)

# Retained and stored, never hashed. Optional: absence must not abort a pull.
METADATA_FIELDS: tuple[str, ...] = ("created_on", "authors", "creator")


class MissingStableFieldsError(ValueError):
    """An upstream record lacks a field STABLE_FIELDS requires."""


# -----------------------------------------------------------------------------#
# SIGNED-URL SCRUB
# -----------------------------------------------------------------------------#
# Rich-text fields (materials_text, guidelines, ...) embed attachments as URLs
# carrying AWS/CloudFront signing params that are regenerated on every request.
# They are credentials, not protocol content — left in, the hash changes nightly
# for any protocol with an attached file.
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

# The separator inside an embedded document is the literal text `&`, not a
# decoded `&` — hence the backslash in the value's excluded set. Requiring a
# leading separator keeps prose like "Expires=" in a protocol from being hit.
_SIGNED_PARAM = re.compile(
    r"([?&]|\\u0026)("
    + "|".join(re.escape(p) for p in VOLATILE_URL_PARAMS)
    + r")=[^&\"'\s\\]*",
    re.IGNORECASE,
)


def scrub_signed_urls(value):
    """Blank the value of every signing parameter, leaving structure intact."""
    if isinstance(value, str):
        return _SIGNED_PARAM.sub(r"\1\2=", value)
    if isinstance(value, dict):
        return {k: scrub_signed_urls(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_signed_urls(v) for v in value]
    return value

# -----------------------------------------------------------------------------#
# DATA PREPARATION
# -----------------------------------------------------------------------------#

def _normalize(obj: Any) -> Any:
    """Recursively NFC-normalize every string, key or value."""
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, dict):
        return {_normalize(k): _normalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalize(v) for v in obj]
    return obj


def canonical_json(obj: Any) -> bytes:
    """Serialize to deterministic UTF-8 bytes.

    Sorted keys, no whitespace, ASCII-escaped, NFC strings. Rejects NaN/Infinity.
    """
    return json.dumps(
        _normalize(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


# -----------------------------------------------------------------------------#
# HASHING
# -----------------------------------------------------------------------------#
def hash_bytes(blob: bytes) -> str:
    """SHA256 hexdigest of already-canonical bytes."""
    return hashlib.sha256(blob).hexdigest()


def content_hash(obj: Any) -> str:
    """SHA256 hexdigest of the canonical serialization."""
    return hash_bytes(canonical_json(obj))


# -----------------------------------------------------------------------------#
# SELECT / HASH
# -----------------------------------------------------------------------------#
def select_protocol(
    protocol: dict,
    include_fields: Sequence[str] | None = None,
    metadata_fields: Sequence[str] | None = None,
) -> dict:
    """Keep the hashed fields (required) plus the metadata fields (optional)."""
    if include_fields is None:
        include_fields = STABLE_FIELDS
    if metadata_fields is None:
        metadata_fields = METADATA_FIELDS

    missing = [field for field in include_fields if field not in protocol]
    if missing:
        identity = protocol.get("id", protocol.get("guid", "<unidentified>"))
        raise MissingStableFieldsError(
            f"protocol {identity!r} is missing required field(s): "
            f"{', '.join(missing)}"
        )

    selected = {field: protocol[field] for field in include_fields}
    selected.update(
        {field: protocol[field] for field in metadata_fields if field in protocol}
    )
    return selected


def hashable_content(
    selected: dict, include_fields: Sequence[str] | None = None
) -> dict:
    """Drop metadata and scrub signing params, leaving only stable identity."""
    if include_fields is None:
        include_fields = STABLE_FIELDS
    return {
        field: scrub_signed_urls(selected[field])
        for field in include_fields
        if field in selected
    }


def protocol_blob(
    selected: dict, include_fields: Sequence[str] | None = None
) -> bytes:
    """Canonical bytes of a protocol's identity fields — the form that is stored."""
    return canonical_json(hashable_content(selected, include_fields))


def protocol_hash(
    selected: dict, include_fields: Sequence[str] | None = None
) -> str:
    """Content hash of a selected protocol, metadata excluded."""
    return hash_bytes(protocol_blob(selected, include_fields))
