# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime
from typing import NamedTuple

from compose.compose import METADATA_FIELDS, ProtocolPipeline
from utils.dates import get_timestamp, to_epoch
from utils.hashing import as_column, canonical_json, hash_bytes
from utils.intervals import (
    VersionInterval,
    close_intervals,
    diff_entries,
    open_intervals,
    seen_before,
    versions_on_date,
)
from utils.intervals import (
    active_hashes as _active_hashes,
)
from utils.store import (
    connect,
    fetch_rows,
    initialize_db,
    insert_statement,
    verify_blobs,
)

# -----------------------------------------------------------------------------#
# WHICH TABLES COMPOSE OWNS
# -----------------------------------------------------------------------------#
# A pipeline is versioned by the same interval rules as a protocol; only the
# table names and the id column differ.
CONTENT_TABLE = "pipeline_content"
HISTORY_TABLE = "pipeline_history"
ID_COLUMN = "pipeline_guid"


# -----------------------------------------------------------------------------#
# ERROR TYPE
# -----------------------------------------------------------------------------#
class DuplicatePipelineGuidError(ValueError):
    """One write carried two different versions of the same pipeline_guid."""


class UnknownPipelineHashError(ValueError):
    """A requested hash is not in pipeline_content."""


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


def initialize_pipeline_db(db: str) -> None:
    """Create the pipeline content/history tables and their indexes if absent."""
    initialize_db(db, SCHEMA)


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
    executor: str | None
    DAG: str
    pipeline: str
    created_on: int | None
    creator: str | None
    valid_from: int


# Derived, not restated — same guard as seal's.
_CONTENT_COLUMNS: tuple[str, ...] = (
    "hash",
    "pipeline_guid",
    "title",
    "manifest_hash",
    "root",
    "executor",
    "DAG",
    "pipeline",
) + METADATA_FIELDS


def build_pipeline_entry(
    artefact: ProtocolPipeline, pulled_at: int | None = None
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


def format_db_entry(
    artefacts: Iterable[ProtocolPipeline], pulled_at: int | None = None
) -> list[PipelineEntry]:
    """Make a list of entries from a bunch of pipelines."""
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
    executor: str | None
    DAG: str
    created_on: int | None
    creator: str | None
    pipeline: str | None = None


def active_pipelines(conn: sqlite3.Connection) -> dict[str, str]:
    """pipeline_guid -> hash for every version currently active."""
    return _active_hashes(conn, HISTORY_TABLE, ID_COLUMN)


def _diff(
    conn: sqlite3.Connection, entries: Iterable[PipelineEntry]
) -> dict[str, list[str]]:
    """Group a write against the active state, for the log."""
    return diff_entries(
        active_pipelines(conn), {row.pipeline_guid: row.hash for row in entries}
    )


# -----------------------------------------------------------------------------#
# GET CONTENT
# -----------------------------------------------------------------------------#
_READ_COLUMNS: tuple[str, ...] = PipelineContentEntry._fields[:-1]


def get_pipelines(
    db: str, hashes: Iterable[str], with_blob: bool = True
) -> list[PipelineContentEntry]:
    """Pipelines by hash, in the order asked for. Any absence raises."""
    wanted = list(hashes)
    if not wanted:
        return []
    columns = _READ_COLUMNS + ("pipeline",) if with_blob else _READ_COLUMNS
    with connect(db, read_only=True) as conn:
        found = {
            key: PipelineContentEntry(*row)
            for key, row in fetch_rows(
                conn, CONTENT_TABLE, columns, "hash", wanted
            ).items()
        }
    missing = sorted(set(wanted) - set(found))
    if missing:
        raise UnknownPipelineHashError(f"not in pipeline_content: {', '.join(missing)}")
    return [found[h] for h in wanted]


def pipelines_on_date(
    conn: sqlite3.Connection, when: int | float | str | date | datetime
) -> dict[str, list[VersionInterval]]:
    """pipeline_guid -> every version active on `when`'s UTC day."""
    return versions_on_date(conn, HISTORY_TABLE, ID_COLUMN, when)


def diff_pipelines(db: str, rows: Iterable[PipelineEntry]) -> dict[str, list[str]]:
    """Compare a set of pipelines against the active state."""
    with connect(db, read_only=True) as conn:
        return _diff(conn, rows)


# -----------------------------------------------------------------------------#
# WRITE CONTENT
# -----------------------------------------------------------------------------#
_INSERT_CONTENT = insert_statement(CONTENT_TABLE, _CONTENT_COLUMNS)


def write_pipeline(
    db: str, entries: list[PipelineEntry], pulled_at: int | None = None
) -> dict[str, list[str]]:
    """Apply one set of pipelines and return its diff."""
    pulled_at = pulled_at if pulled_at is not None else get_timestamp()

    seen = {row.pipeline_guid for row in entries}
    if len(seen) != len(entries):
        raise DuplicatePipelineGuidError(
            "a write carried two versions of the same pipeline_guid; "
            "at most one version of a pipeline may be active"
        )

    with connect(db) as conn:
        diff = _diff(conn, entries)
        first_time = set(diff["new"]) - seen_before(
            conn, HISTORY_TABLE, ID_COLUMN, diff["new"]
        )
        opening = set(diff["new"]) | set(diff["changed"])
        closing = set(diff["changed"]) | set(diff["absent"])
        fresh = [row for row in entries if row.pipeline_guid in opening]

        # Bound by name, so valid_from riding along unreferenced is harmless.
        conn.executemany(_INSERT_CONTENT, [row._asdict() for row in fresh])
        close_intervals(conn, HISTORY_TABLE, ID_COLUMN, closing, pulled_at)
        open_intervals(
            conn,
            HISTORY_TABLE,
            ID_COLUMN,
            [
                (
                    row.pipeline_guid,
                    row.hash,
                    row.valid_from if row.pipeline_guid in first_time else pulled_at,
                )
                for row in fresh
            ],
        )
    return diff


# -----------------------------------------------------------------------------#
# VERIFY CONTENT
# -----------------------------------------------------------------------------#
def verify_pipelines(db: str) -> list[str]:
    """Return hashes whose stored blob no longer hashes to its own key."""
    return verify_blobs(db, CONTENT_TABLE, "hash", "pipeline")
