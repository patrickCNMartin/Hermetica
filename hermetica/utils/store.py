# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager

from utils.dates import get_timestamp
from utils.error_handling import MissingHash
from utils.hashing import hash_bytes


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
    """Run a schema's statements against `db`. Each must be IF NOT EXISTS."""
    with connect(db) as conn:
        for statement in schema:
            conn.execute(statement)


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
        raise MissingHash(f"not in {table}: {', '.join(missing)}")
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
