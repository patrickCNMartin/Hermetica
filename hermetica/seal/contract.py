# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import hashlib
import json
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any

# -----------------------------------------------------------------------------#
# CONTENT CONTRACT
# -----------------------------------------------------------------------------#
# Specify the fields that are going to hashed and version controlled
# Essentially my whitelisted .gitignroe contract
HASH_FIELDS: tuple[str, ...] = (
    "doi",
    "reserved_doi",
    "id",
    "guid",
    "title",
    "description",
    "guidelines",
    "before_start",
    "disclaimer",
    "warning",
    "materials",
    "steps",
    "chain",
    "units",
    "uri",
    "version_class",
    "protocol_references",
)
# Specify other in
METADATA_FIELDS: tuple[str, ...] = (
    "created_on",
    "creator",
    "authors",
    "keywords",
)

# A source adapter reshapes its platform's response into this template; the
# nested requests needed to fill it are the adapter's problem, not seal's.
PROTOCOL_FIELDS: tuple[str, ...] = HASH_FIELDS + METADATA_FIELDS


# The template itself. Frozen: an artefact is a snapshot of upstream content,
# not a working buffer — mutating one after hashing would desync blob and hash.
@dataclass(frozen=True, slots=True)
class ProtocolArtefact:
    # --- hashed (HASH_FIELDS) ---------------------------------------------- #
    id: int
    guid: str
    title: str
    description: str
    guidelines: str
    before_start: str
    disclaimer: str
    warning: str
    materials: str
    steps: list[dict]
    chain: list[int]
    units: dict[str, str]
    uri: str
    doi: str
    reserved_doi: str
    version_class: int
    protocol_references: str
    # --- retained, never hashed (METADATA_FIELDS) -------------------------- #
    created_on: int
    keywords: str
    authors: list[dict] | None = None
    creator: dict | None = None

    def to_dict(self) -> dict:
        """Full artefact as a plain dict — the stored/metadata-bearing form."""
        return asdict(self)

    def hashable(self) -> dict:
        """Only the fields HASH_FIELDS declares — the form that gets hashed."""
        return {field: getattr(self, field) for field in HASH_FIELDS}

    def metadata(self) -> dict:
        """Get meta data fields"""
        return {field: getattr(self, field) for field in METADATA_FIELDS}


# Hashing algortihm
HASH_ALGORITHM = "sha256"


# -----------------------------------------------------------------------------#
# RICH TEXT
# -----------------------------------------------------------------------------#
# Shared by the writer and the reader: a source adapter parses envelopes to find
# the units it must resolve at pull time, and scribe parses the same envelopes to
# render them. Kept here so neither has to import the other.
def parse_rich_text(value: Any) -> dict | None:
    """A Draft.js envelope, or None when the field holds no rich text."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip().startswith("{"):
        return None
    # Anything shaped like an envelope but unparseable is corrupt: silently
    # skipping it would drop units from a hashed field.
    return json.loads(value)


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


# actual protocol that is going to be stored
def protocol_blob(artefact: ProtocolArtefact) -> bytes:
    """Prepare protocol blob from protocol artefact"""
    protocol = artefact.hashable()
    return canonical_json(protocol)


# Hash fingerprint for thhat protocol
def protocol_hash(
    artefact: ProtocolArtefact,
) -> str:
    """Content hash of a selected protocol, metadata excluded."""
    return hash_bytes(protocol_blob(artefact))
