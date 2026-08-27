# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from typing import NamedTuple

from compose.compose import ProtocolPipeline
from seal.dates import get_timestamp
from seal.store import connect

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
    "CREATE INDEX IF NOT EXISTS idx_pipeline_content_id ON pipeline_content (guid)",
)


def initialize_pipeline_db(db: str) -> None:
    """Init emtpy database and if template is available fill that in"""
    with connect(db) as conn:
        for statement in SCHEMA:
            conn.execute(statement)


# -----------------------------------------------------------------------------#
# FORMATTING DB ENTRIES
# -----------------------------------------------------------------------------#
# Tyep enfore a pipeline entry


class PipelineEntry(NamedTuple):
    hash: str
    guid: str
    title: str
    root: str
    executor: str
    DAG: dict
    created_on: int | None
    creator: str | None


def build_pipeline_entry(
    artefact: ProtocolPipeline, pulled_at: int | None = None
) -> PipelineEntry:
    pulled_at = pulled_at if pulled_at is not None else get_timestamp()
    # DAG = canonical_json(artefact.DAG)

    return 0
