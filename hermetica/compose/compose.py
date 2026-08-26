# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import sqlite3
from dataclasses import asdict, dataclass

from seal.store import active_hashes, connect, initialize_db

# -----------------------------------------------------------------------------#
# CREATE COMPOSITION TEMPLATE
# -----------------------------------------------------------------------------#
HASH_FIELDS: tuple[str, ...] = (
    "guid",
    "title",
    "manifest_hash",
    "DAG",
    "DAG_ids",
    "executor",
    "root",
)
# Specify other in
METADATA_FIELDS: tuple[str, ...] = (
    "created_on",
    "creator",
)

# Hashing algortihm
HASH_ALGORITHM = "sha256"

COMPOSITION_FIELDS: tuple[str, ...] = HASH_FIELDS + METADATA_FIELDS


@dataclass(frozen=True, slots=True)
class ProtocolPipeline:
    # --- hashed (HASH_FIELDS) ---------------------------------------------- #
    guid: int
    title: str
    manifest_hash: str
    root: str  # starting material/ sample type
    executor: str  # human or robot?
    DAG: dict
    # --- retained, never hashed (METADATA_FIELDS) -------------------------- #
    created_on: int
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

# -----------------------------------------------------------------------------#
# BUILD PROTOCOL PIPELINE DB
# -----------------------------------------------------------------------------#
SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS pipeline_content (
        hash             TEXT PRIMARY KEY,
        guid             TEXT NOT NULL
        title            TEXT NOT NULL,
        root             TEXT NOT NULL
        executor         TEXT NOT NULL
        DAG              TEXT NOT NULL
        created_on       INTEGER,
        creator          TEXT,
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pipeline_content_id "
    "ON pipeline_content (guid)",
)


def initialize_pipeline_db(db: str) -> None:
    """Create the content/history/snapshot tables and their indexes if absent."""
    with connect(db) as conn:
        for statement in SCHEMA:
            conn.execute(statement)
