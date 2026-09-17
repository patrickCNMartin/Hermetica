# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from collections.abc import Iterable
from typing import NamedTuple

from compose.compose import PipelineArtefact
from utils.constants import (
    PIPELINE_CONTENT,
    PIPELINE_CONTENT_FIELDS,
    PIPELINE_GUID,
    PIPELINE_HISTORY,
)
from utils.dates import get_timestamp, to_epoch
from utils.hashing import canonical_json, encode_entry, hash_bytes
from utils.intervals import (
    active_hashes,
    close_intervals,
    version_control_diff,
    write_version_control,
)
from utils.store import (
    append_only_triggers,
    connect,
    fetch_entries,
    immutable_triggers,
    insert_statement,
)

# -----------------------------------------------------------------------------#
# BUILD PROTOCOL PIPELINE DB
# -----------------------------------------------------------------------------#


class InactivePipelineError(ValueError):
    """Retiring a pipeline that has no active version."""

    def __init__(self, guid: str):
        self.guid = guid
        super().__init__(f"pipeline {guid} has no active version to retire")


SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS pipeline_content (
        hash             TEXT PRIMARY KEY,
        pipeline_guid    TEXT NOT NULL,
        title            TEXT NOT NULL,
        manifest_hash    TEXT,
        root             TEXT,
        DAG              TEXT NOT NULL,
        nodes            TEXT NOT NULL,
        node_hashes      TEXT NOT NULL,
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
    # One active version per pipeline, mirroring idx_history_one_active. The
    # portal is a second writer on this file, which is what makes a Python-only
    # invariant insufficient here.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_pipeline_history_one_active "
    "ON pipeline_history (pipeline_guid) WHERE deprecated_at IS NULL",
    *immutable_triggers(PIPELINE_CONTENT),
    *append_only_triggers(PIPELINE_HISTORY, ("pipeline_guid", "hash", "valid_from")),
)


# -----------------------------------------------------------------------------#
# FORMATTING DB ENTRIES
# -----------------------------------------------------------------------------#
# Type enforce a pipeline entry


class PipelineEntry(NamedTuple):
    hash: str
    pipeline_guid: str
    title: str
    manifest_hash: str | None
    root: str | None
    DAG: str
    nodes: str
    node_hashes: str
    pipeline: str
    created_on: int | None
    creator: str | None
    valid_from: int


def build_pipeline_entry(
    artefact: PipelineArtefact, pulled_at: int | None = None
) -> PipelineEntry:
    """Prepare one pipeline entry from a PipelineArtefact."""
    pulled_at = pulled_at if pulled_at is not None else get_timestamp()
    blob = canonical_json(artefact.hashable())
    metadata = {k: encode_entry(v) for k, v in artefact.metadata().items()}
    created_on = metadata["created_on"]
    return PipelineEntry(
        hash=hash_bytes(blob),
        pipeline_guid=str(artefact.guid),
        title=artefact.title,
        manifest_hash=artefact.manifest_hash,
        root=artefact.root,
        DAG=encode_entry(artefact.DAG),
        nodes=encode_entry(artefact.nodes),
        node_hashes=encode_entry(artefact.node_hashes),
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


class PipelineContentEntry(NamedTuple):
    hash: str
    pipeline_guid: str
    title: str
    manifest_hash: str | None
    root: str | None
    DAG: str
    nodes: str
    node_hashes: str
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
    """Compare a set of pipelines against the active state. Never reports absence."""
    return version_control_diff(
        db, pipeline_history, pipeline_guid, pipelines, absence=False
    )


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
    """Apply one set of pipelines and return its diff.

    A pipeline is edited one at a time, not pulled as a snapshot, so a pipeline
    left out of this write is not gone. Retiring is `retire_pipeline`.
    """
    insert = insert_statement(pipeline_content, pipleline_content_fields)
    return write_version_control(
        db, pipeline_history, pipeline_guid, insert, entries, pulled_at, absence=False
    )


def retire_pipeline(
    db: str,
    guid: str,
    retired_at: int | None = None,
    pipeline_history: str = PIPELINE_HISTORY,
    pipeline_guid: str = PIPELINE_GUID,
) -> str:
    """Close a pipeline's active interval. Returns the hash that was retired.

    Its content and history stay; writing it again opens a new interval.
    """
    retired_at = retired_at if retired_at is not None else get_timestamp()
    with connect(db) as conn:
        active = active_hashes(
            conn, pipeline_history, pipeline_guid, (pipeline_guid, guid)
        )
        if guid not in active:
            raise InactivePipelineError(guid)
        close_intervals(conn, pipeline_history, pipeline_guid, [guid], retired_at)
    return active[guid]
