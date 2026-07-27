# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import hashlib
import json
import unicodedata
from typing import Any

# -----------------------------------------------------------------------------#
# CANONICAL FORM
# -----------------------------------------------------------------------------#
HASH_ALGORITHM = "sha256"
UNICODE_FORM = "NFC"


def _normalize(obj: Any) -> Any:
    """Recursively NFC-normalize every string, key or value."""
    if isinstance(obj, str):
        return unicodedata.normalize(UNICODE_FORM, obj)
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
# HASH
# -----------------------------------------------------------------------------#
def hash_bytes(blob: bytes) -> str:
    """SHA256 hexdigest of already-canonical bytes."""
    return hashlib.sha256(blob).hexdigest()


def content_hash(obj: Any) -> str:
    """SHA256 hexdigest of the canonical serialization."""
    return hash_bytes(canonical_json(obj))
