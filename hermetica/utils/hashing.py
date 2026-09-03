# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import hashlib
import json
import unicodedata
from typing import Any

# Hashing algorithm
HASH_ALGORITHM = "sha256"


# -----------------------------------------------------------------------------#
# CANONICAL FORM
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
    return f"{HASH_ALGORITHM}:{hashlib.sha256(blob).hexdigest()}"


def hash_of(payload: Any) -> str:
    """Content hash of a payload that is not already canonical bytes."""
    return hash_bytes(canonical_json(payload))


# -----------------------------------------------------------------------------#
# COLUMN COERCION
# -----------------------------------------------------------------------------#
def as_column(value: Any) -> Any:
    """Anything sqlite cannot store natively becomes canonical JSON text."""
    if value is None or isinstance(value, (int, float, str)):
        return value
    return canonical_json(value).decode("ascii")
