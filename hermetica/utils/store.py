# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager

from utils.dates import get_timestamp
from utils.hashing import hash_bytes


class MissingHash(ValueError):
    """Hash value not found in the database."""

    def __init__(self, table: str, missing: list[str]):
        self.table, self.missing = table, missing
        super().__init__(f"not in {table}: {', '.join(missing)}")


# -----------------------------------------------------------------------------#
# CONNECTION
# -----------------------------------------------------------------------------#
@contextmanager
def connect(db: str, read_only: bool = False) -> Iterator[sqlite3.Connection]:
    """
    Open a connection, commit or roll back, and always close it.
    """
    uri = f"file:{db}?mode=ro" if read_only else db
    conn = sqlite3.connect(uri, uri=read_only)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            yield conn
    finally:
        conn.close()


# -----------------------------------------------------------------------------#
# SCHEMA
# -----------------------------------------------------------------------------#
def initialize_db(db: str, schema: Iterable[str]) -> None:
    """Run a schema's statements against `db`. Each must be IF NOT EXISTS.

    WAL is set here because it is stored in the file, not the connection: the
    nightly writer and the API's readers then stop blocking each other. It needs
    every process on one host — a shared volume, never a network filesystem.
    """
    with connect(db) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        for statement in schema:
            conn.execute(statement)


# -----------------------------------------------------------------------------#
# WRITE PROTECTION
# -----------------------------------------------------------------------------#
# In the schema rather than in Python, because a PRAGMA is per-connection and
# `sqlite3 the.db` sets none of ours. A trigger binds whoever opens the file.
def _abort_trigger(table: str, event: str, reason: str, when: str = "") -> str:
    """A BEFORE trigger that refuses `event`. `when` narrows it to the illegal
    cases; empty refuses every one. RAISE(ABORT) reaches Python as
    sqlite3.IntegrityError carrying `reason`."""
    guard = f"WHEN {when} " if when else ""
    return (
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_{event.lower()} "
        f"BEFORE {event} ON {table} {guard}"
        f"BEGIN SELECT RAISE(ABORT, '{table}: {reason}'); END"
    )


def immutable_triggers(table: str) -> tuple[str, str]:
    """Nothing in `table` is ever updated or deleted.

    For a content table the row is addressed by the hash of its own bytes, so
    an edit is a lie — the row would no longer be what its key says it is.
    """
    return (
        _abort_trigger(table, "DELETE", "content is never deleted"),
        _abort_trigger(
            table, "UPDATE", "content is addressed by its hash, never edited"
        ),
    )


def append_only_triggers(table: str, immutable: tuple[str, ...]) -> tuple[str, str]:
    """No deletes, and the only legal UPDATE is closing an open interval:
    `deprecated_at` NULL -> a timestamp, every `immutable` column untouched.

    `IS NOT` is null-safe inequality, so a NULL column cannot slip through.
    """
    frozen = " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in immutable)
    return (
        _abort_trigger(table, "DELETE", "history is never deleted"),
        _abort_trigger(
            table,
            "UPDATE",
            "the only legal update is closing an open interval",
            when=(
                "OLD.deprecated_at IS NOT NULL "  # already closed
                "OR NEW.deprecated_at IS NULL "  # reopening
                f"OR {frozen}"  # rewriting the row's identity
            ),
        ),
    )


# -----------------------------------------------------------------------------#
# READ
# -----------------------------------------------------------------------------#
def format_entries(build: Callable, artefacts: Iterable, pulled_at: int | None) -> list:
    pulled_at = pulled_at if pulled_at is not None else get_timestamp()
    return [build(artefact, pulled_at) for artefact in artefacts]


def insert_statement(table: str, columns: tuple[str, ...]) -> str:
    """INSERT OR IGNORE bound by name, so a reordered row cannot misalign."""
    return (
        f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(':' + column for column in columns)})"
    )


def fetch_entries(
    db: str,
    table: str,
    columns: tuple[str, ...],
    key_column: str,
    keys: Iterable[str],
    entry_type: type,
) -> list:
    wanted = list(keys)
    if not wanted:
        return []
    with connect(db, read_only=True) as conn:
        found = {
            key: entry_type(*row)
            for key, row in fetch_entry(
                conn, table, columns, key_column, wanted
            ).items()
        }
    missing = sorted(set(wanted) - set(found))
    if missing:
        raise MissingHash(table, missing)
    return [found[key] for key in wanted]


def fetch_entry(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    key_column: str,
    keys: list[str],
) -> dict[str, tuple]:
    at = columns.index(key_column)
    if not keys:
        return {}
    slots = ",".join("?" * len(keys))
    return {
        row[at]: row
        for row in conn.execute(
            f"SELECT {', '.join(columns)} FROM {table} WHERE {key_column} IN ({slots})",
            keys,
        )
    }


# -----------------------------------------------------------------------------#
# VERIFY
# -----------------------------------------------------------------------------#
def verify_blobs(db: str, table: str, hash_column: str, blob_column: str) -> list[str]:
    """Return hashes whose stored blob no longer hashes to its own key."""
    with connect(db, read_only=True) as conn:
        return [
            stored_hash
            for stored_hash, blob in conn.execute(
                f"SELECT {hash_column}, {blob_column} FROM {table}"
            )
            if hash_bytes(blob.encode("ascii")) != stored_hash
        ]
