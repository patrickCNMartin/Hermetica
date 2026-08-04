# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import sqlite3
from dataclasses import asdict, dataclass

from seal.store import active_hashes, connect

# -----------------------------------------------------------------------------#
# CREATE COMPOSITION TEMPLATE
# -----------------------------------------------------------------------------#
HASH_FIELDS: tuple[str, ...] = ("id", "guid", "title", "manifest_hash", "DAG")
# Specify other in
METADATA_FIELDS: tuple[str, ...] = (
    "created_on",
    "creator",
    "last_modified_on",
)


COMPOSITION_FIELDS: tuple[str, ...] = HASH_FIELDS + METADATA_FIELDS


# Note on DAG and cat_DAG.
# One thing that I have to figure out is how I can actually build the DAG
# There are general categories and in those cats there are the actual protocols
# sometimes there are a lot of them and sometimes they are empty.
# So the cat_DAG is the easy flow since it represent the broad categories
# but the actual protocols are nested within these categories and are conditional
# Not too sure what the best way to work with this.
@dataclass(frozen=True, slots=True)
class ComposedProtocols:
    # --- hashed (HASH_FIELDS) ---------------------------------------------- #
    id: int
    guid: int
    title: str
    manifest_hash: str
    root: str  # starting material/ sample type
    executor: str  # human or robot?
    cat_DAG: dict  # placeholder for now
    DAG: dict
    # --- retained, never hashed (METADATA_FIELDS) -------------------------- #
    created_on: int
    last_modified_on: int
    creator: dict

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
# FETCH PROTOCOLS
# -----------------------------------------------------------------------------#


def active_protocols(db_path: str) -> dict[str, str]:
    """hash -> title for every protocol version currently active."""
    with connect(db_path, read_only=True) as conn:
        protocol_set = list(active_hashes(conn).values())
        if not protocol_set:
            return {}
        hash_list = ",".join("?" * len(protocol_set))
        cursor = conn.cursor()
        cursor.row_factory = sqlite3.Row
        protocols = {
            row["hash"]: row["title"]
            for row in cursor.execute(
                f"SELECT hash, title FROM protocol_content WHERE hash IN ({hash_list})",
                protocol_set,
            )
        }
    return protocols
