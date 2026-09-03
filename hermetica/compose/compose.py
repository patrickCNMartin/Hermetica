# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import sqlite3
from dataclasses import asdict, dataclass

from utils.intervals import active_hashes
from utils.store import connect
from utils.constants import (
    PIPELINE_HASH_FIELDS,
    PIPELINE_METADATA_FIELDS,
    PIPELINE_HISTORY,
    PIPELINE_GUID)


# -----------------------------------------------------------------------------#
# CREATE COMPOSITION TEMPLATE
# -----------------------------------------------------------------------------#

@dataclass(frozen=True)
class PipelineArtefact:
    # --- hashed (HASH_FIELDS) ---------------------------------------------- #
    guid: str
    title: str
    # None until the pipeline is pinned to a manifest; a base template is not.
    manifest_hash: str | None
    root: str | None  # starting material/ sample type
    executor: str | None  # human or robot?
    DAG: dict
    # --- retained, never hashed (METADATA_FIELDS) -------------------------- #
    created_on: int
    creator: dict | str | None = None

    def to_dict(self) -> dict:
        """Full artefact as a plain dict — the stored/metadata-bearing form."""
        return asdict(self)

    def hashable(self) -> dict:
        """Only the fields HASH_FIELDS declares — the form that gets hashed."""
        return {field: getattr(self, field) for field in PIPELINE_HASH_FIELDS}

    def metadata(self) -> dict:
        """Get meta data fields"""
        return {field: getattr(self, field) for field in PIPELINE_METADATA_FIELDS}


# -----------------------------------------------------------------------------#
# FETCH PROTOCOLS
# -----------------------------------------------------------------------------#


def active_protocols(db_path: str) -> dict[str, str]:
    """hash -> title for every protocol version currently active."""
    with connect(db_path, read_only=True) as conn:
        protocol_set = list(active_hashes(conn, PIPELINE_HISTORY, PIPELINE_GUID).values())
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

