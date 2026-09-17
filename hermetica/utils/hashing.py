# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import hashlib
import json
import unicodedata
from typing import Any

from utils.constants import HASH_ALGORITHM


# -----------------------------------------------------------------------------#
# CANONICAL FORM
# -----------------------------------------------------------------------------#
def normalize(obj: Any) -> Any:
    """Recursively NFC-normalize every string, key or value."""
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, dict):
        return {normalize(k): normalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [normalize(v) for v in obj]
    return obj


def canonical_json(obj: Any) -> bytes:
    """Serialize to deterministic UTF-8 bytes.

    Sorted keys, no whitespace, ASCII-escaped, NFC strings. Rejects NaN/Infinity.
    """
    return json.dumps(
        normalize(obj),
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
def encode_entry(value: Any) -> Any:
    """Numbers stay numbers for INTEGER columns; everything else is JSON text.

    A string is encoded too, or `decode_entry` could not tell `"Ada"` from a
    JSON document and a plain-string creator would never read back.
    """
    if value is None or (
        isinstance(value, (int, float)) and not isinstance(value, bool)
    ):
        return value
    return canonical_json(value).decode("ascii")


def decode_entry(value: str | None) -> Any:
    return None if value is None else json.loads(value)
