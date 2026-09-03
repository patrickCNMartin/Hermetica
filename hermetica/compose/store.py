# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from collections.abc import Iterable
from dataclasses import dataclass

from compose.compose import PipelineArtefact
from utils.constants import (
    PIPELINE_CONTENT,
    PIPELINE_CONTENT_FIELDS,
    PIPELINE_GUID,
    PIPELINE_HISTORY,
)
from utils.dates import get_timestamp, to_epoch
from utils.hashing import as_column, canonical_json, hash_bytes
from utils.intervals import diff_versioned, write_version_control
from utils.store import fetch_entries, insert_statement

# -----------------------------------------------------------------------------#
# BUILD PROTOCOL PIPELINE DB
# -----------------------------------------------------------------------------#
SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS pipeline_content (
        hash             TEXT PRIMARY KEY,
        pipeline_guid    TEXT NOT NULL,
        title            TEXT NOT NULL,
        manifest_hash    TEXT,
        root             TEXT,
        executor         TEXT,
        DAG              TEXT NOT NULL,
        pipeline         TEXT NOT NULL,
        created_on       INTEGER,
        creator          TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pipeline_history (
        pipeline_guid TEXT NOT NULL,
        hash          TEXT NOT NULL REFERENCES pipeline_content(hash),
        valid_from    INTEGER NOT NULL,
        deprecated_at INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pipeline_content_guid "
    "ON pipeline_content (pipeline_guid)",
    "CREATE INDEX IF NOT EXISTS idx_pipeline_history_guid "
    "ON pipeline_history (pipeline_guid)",
    "CREATE INDEX IF NOT EXISTS idx_pipeline_history_validity "
    "ON pipeline_history (valid_from, deprecated_at)",
)


# -----------------------------------------------------------------------------#
# FORMATTING DB ENTRIES
# -----------------------------------------------------------------------------#
# Type enforce a pipeline entry
@dataclass(frozen=True)
class PipelineEntry:
    hash: str
    pipeline_guid: str
    title: str
    manifest_hash: str | None
    root: str | None
    executor: str | None
    DAG: str
    pipeline: str
    created_on: int | None
    creator: str | None
    valid_from: int


def build_pipeline_entry(
    artefact: PipelineArtefact, pulled_at: int | None = None
) -> PipelineEntry:
    """Prepare one pipeline entry from a ProtocolPipeline."""
    pulled_at = pulled_at if pulled_at is not None else get_timestamp()
    blob = canonical_json(artefact.hashable())
    metadata = {k: as_column(v) for k, v in artefact.metadata().items()}
    created_on = metadata["created_on"]
    return PipelineEntry(
        hash=hash_bytes(blob),
        pipeline_guid=str(artefact.guid),
        title=artefact.title,
        manifest_hash=artefact.manifest_hash,
        root=artefact.root,
        executor=artefact.executor,
        DAG=as_column(artefact.DAG),
        pipeline=blob.decode("ascii"),
        valid_from=to_epoch(created_on) if created_on else pulled_at,
        **metadata,
    )


def format_pipeline_entry(
    artefacts: Iterable[PipelineArtefact], pulled_at: int | None = None
) -> list[PipelineArtefact]:
    """make a list of entries from a bunch of protocols"""
    pulled_at = pulled_at if pulled_at is not None else get_timestamp()
    return [build_pipeline_entry(artefact, pulled_at) for artefact in artefacts]


# -----------------------------------------------------------------------------#
# CHANGE DETECTION UTILS
# -----------------------------------------------------------------------------#
@dataclass(frozen=True)
class PipelineContentEntry:
    hash: str
    pipeline_guid: str
    title: str
    manifest_hash: str | None
    root: str | None
    executor: str | None
    DAG: str
    created_on: int | None
    creator: str | None
    pipeline: str | None = None


# -----------------------------------------------------------------------------#
# GET CONTENT
# -----------------------------------------------------------------------------#
# don't like this but I hate the constant approach even more.
def read_pipeline_content():
    return PipelineContentEntry._fields[:-1]


# I know I don't need to parse pipeline content as an argument
# But I hate when function pull something out of nothing instead of
# parsing it as an argument.
# explicit IN and explicit OUT
def get_pipelines(
    db: str,
    hashes: Iterable[str],
    with_blob: bool = True,
    content_table: str = PIPELINE_CONTENT,
) -> list[PipelineContentEntry]:
    READ_COLUMNS = read_pipeline_content()
    columns = READ_COLUMNS + ("pipeline",) if with_blob else READ_COLUMNS
    return fetch_entries(
        db, content_table, columns, "hash", hashes, PipelineContentEntry
    )


def diff_pipelines(
    db: str,
    pipelines: Iterable[PipelineEntry],
    pipeline_history: str = PIPELINE_HISTORY,
    pipeline_guid: str = PIPELINE_GUID,
) -> dict[str, list[str]]:
    """Compare a set of pipelines against the active state."""
    return diff_versioned(db, pipeline_history, pipeline_guid, pipelines)


# -----------------------------------------------------------------------------#
# WRITE CONTENT
# -----------------------------------------------------------------------------#
def write_pipeline(
    db: str,
    entries: list[PipelineEntry],
    pulled_at: int | None = None,
    pipeline_content: str = PIPELINE_CONTENT,
    pipleline_content_fields: Iterable[str] = PIPELINE_CONTENT_FIELDS,
    pipeline_history: str = PIPELINE_HISTORY,
    pipeline_guid: str = PIPELINE_GUID,
) -> dict[str, list[str]]:
    """Apply one set of pipelines and return its diff."""
    insert = insert_statement(pipeline_content, pipleline_content_fields)
    return write_version_control(
        db, pipeline_history, pipeline_guid, insert, entries, pulled_at
    )
