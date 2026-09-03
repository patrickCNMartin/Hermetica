# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import json
from dataclasses import asdict, dataclass
from typing import Any

from utils.hashing import hash_of
from utils.constants import PROTOCOL_HASH_FIELDS, PROTOCOL_METADATA_FIELDS
# -----------------------------------------------------------------------------#
# CONTENT CONTRACT
# -----------------------------------------------------------------------------#
@dataclass(frozen=True)
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
        return {field: getattr(self, field) for field in PROTOCOL_HASH_FIELDS}

    def metadata(self) -> dict:
        """Get meta data fields"""
        return {field: getattr(self, field) for field in PROTOCOL_METADATA_FIELDS}


# -----------------------------------------------------------------------------#
# RICH TEXT
# -----------------------------------------------------------------------------#
def parse_rich_text(value: Any) -> dict | None:
    """A Draft.js envelope, or None when the field holds no rich text."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip().startswith("{"):
        return None
    return json.loads(value)


# -----------------------------------------------------------------------------#
# HASHING
# -----------------------------------------------------------------------------#
def protocol_hash(artefact: ProtocolArtefact) -> str:
    """Content hash of a selected protocol, metadata excluded."""
    return hash_of(artefact.hashable())
