# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import sqlite3
from collections.abc import Iterable

from utils.dates import get_timestamp
from utils.store import connect


# -----------------------------------------------------------------------------#
# READ
# -----------------------------------------------------------------------------#
def active_hashes(
    conn: sqlite3.Connection,
    table: str,
    id_column: str,
    scope: tuple[str, str] | None = None,
) -> dict[str, str]:
    """id -> hash for every version holding the active slot right now.

    `scope` is a (column, value) pair partitioning the table. Without one,
    absence is computed against every entry — safe only while a single writer
    owns the table, since anything it did not pull looks absent.
    """
    where, params = "deprecated_at IS NULL", ()
    if scope:
        where, params = f"{where} AND {scope[0]} = ?", (scope[1],)
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    return {
        entry[id_column]: entry["hash"]
        for entry in cursor.execute(
            f"SELECT {id_column}, hash FROM {table} WHERE {where}", params
        )
    }


def active_entries(
    db: str,
    history_table: str,
    content_table: str,
    id_column: str,
    columns: tuple[str, ...],
    entry_id: str | None = None,
) -> list[dict]:
    """The active version of every id, or of one: content columns plus the
    interval's `valid_from`, ordered by id."""
    where, params = "history.deprecated_at IS NULL", ()
    if entry_id is not None:
        where, params = f"{where} AND history.{id_column} = ?", (entry_id,)
    selected = ", ".join(f"content.{column}" for column in columns)
    with connect(db, read_only=True) as conn:
        cursor = conn.cursor()
        cursor.row_factory = sqlite3.Row
        return [
            dict(entry)
            for entry in cursor.execute(
                f"SELECT {selected}, history.valid_from FROM {history_table} history "
                f"JOIN {content_table} content ON content.hash = history.hash "
                f"WHERE {where} ORDER BY history.{id_column}",
                params,
            )
        ]


def latest_entries(
    db: str,
    history_table: str,
    content_table: str,
    key_column: str,
    keys: Iterable[str],
    columns: tuple[str, ...],
) -> dict[str, dict]:
    """Each key's most recent version, whether or not it is still active.

    Content columns plus `valid_from` and `deprecated_at` — `deprecated_at` None
    is what active means. A key never stored is simply absent from the result.
    """
    wanted = sorted(set(keys))
    if not wanted:
        return {}
    slots = ",".join("?" * len(wanted))
    selected = ", ".join(f"content.{column}" for column in columns)
    latest: dict[str, dict] = {}
    with connect(db, read_only=True) as conn:
        cursor = conn.cursor()
        cursor.row_factory = sqlite3.Row
        for entry in cursor.execute(
            f"SELECT {selected}, history.valid_from, history.deprecated_at "
            f"FROM {history_table} history "
            f"JOIN {content_table} content ON content.hash = history.hash "
            f"WHERE content.{key_column} IN ({slots}) "
            "ORDER BY history.valid_from, history.rowid",
            wanted,
        ):
            latest[entry[key_column]] = dict(entry)
    return latest


def intervals_of(
    db: str, history_table: str, id_column: str, entry_id: str
) -> list[dict]:
    """Every interval one id has held, oldest first. Empty if it never existed."""
    with connect(db, read_only=True) as conn:
        cursor = conn.cursor()
        cursor.row_factory = sqlite3.Row
        return [
            dict(entry)
            for entry in cursor.execute(
                f"SELECT hash, valid_from, deprecated_at FROM {history_table} "
                f"WHERE {id_column} = ? ORDER BY valid_from, rowid",
                (entry_id,),
            )
        ]


def seen_before(
    conn: sqlite3.Connection, table: str, id_column: str, ids: list[str]
) -> set[str]:
    """Which of these ids already have history, active or closed."""
    if not ids:
        return set()
    slots = ",".join("?" * len(ids))
    return {
        found
        for (found,) in conn.execute(
            f"SELECT DISTINCT {id_column} FROM {table} WHERE {id_column} IN ({slots})",
            ids,
        )
    }


# -----------------------------------------------------------------------------#
# DIFF
# -----------------------------------------------------------------------------#
def incoming_hashes(entries: Iterable, id_column: str) -> dict[str, str]:
    """id -> hash for one pull's worth of entries."""
    return {getattr(entry, id_column): entry.hash for entry in entries}


def diff_entries(
    active: dict[str, str], incoming: dict[str, str], absence: bool = True
) -> dict[str, list[str]]:
    """Group ids as new / changed / unchanged / absent.

    Pure: two id -> hash maps in, four sorted id lists out. `absent` is what
    makes deprecate-on-absence possible — content addressing cannot see it.
    `absence=False` is for writes that are edits, not snapshots: what was not
    written is not gone, so `absent` stays empty.
    """
    new, changed, unchanged = [], [], []
    for entry_id, incoming_hash in incoming.items():
        if entry_id not in active:
            new.append(entry_id)
        elif active[entry_id] != incoming_hash:
            changed.append(entry_id)
        else:
            unchanged.append(entry_id)

    return {
        "new": sorted(new),
        "changed": sorted(changed),
        "unchanged": sorted(unchanged),
        "absent": sorted(set(active) - set(incoming)) if absence else [],
    }


# -----------------------------------------------------------------------------#
# INTERVALS
# -----------------------------------------------------------------------------#
def close_intervals(
    conn: sqlite3.Connection,
    table: str,
    id_column: str,
    ids: Iterable[str],
    at: int,
) -> None:
    """Stamp `deprecated_at` on whatever version each id has open."""
    conn.executemany(
        f"UPDATE {table} SET deprecated_at = ? "
        f"WHERE {id_column} = ? AND deprecated_at IS NULL",
        [(at, entry_id) for entry_id in sorted(ids)],
    )


def open_intervals(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    entries: Iterable[tuple],
) -> None:
    """Open a fresh interval per entry. `deprecated_at` is always NULL — that is
    what open means — so the caller names every other column it fills."""
    slots = ", ".join("?" * len(columns))
    conn.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}, deprecated_at) "
        f"VALUES ({slots}, NULL)",
        list(entries),
    )


# -----------------------------------------------------------------------------#
# WRITING VC
# -----------------------------------------------------------------------------#


def write_version_control(
    db: str,
    history_table: str,
    id_column: str,
    insert_sql: str,
    entries: list,
    pulled_at: int | None,
    scope: tuple[str, str] | None = None,
    absence: bool = True,
) -> dict[str, list[str]]:

    pulled_at = pulled_at if pulled_at is not None else get_timestamp()

    with connect(db) as conn:
        diff = diff_entries(
            active_hashes(conn, history_table, id_column, scope),
            incoming_hashes(entries, id_column),
            absence,
        )
        first_time = set(diff["new"]) - seen_before(
            conn, history_table, id_column, diff["new"]
        )
        opening = set(diff["new"]) | set(diff["changed"])
        closing = set(diff["changed"]) | set(diff["absent"])
        fresh = [
            (getattr(entry, id_column), entry)
            for entry in entries
            if getattr(entry, id_column) in opening
        ]

        # Bound by name, so valid_from riding along unreferenced is harmless.
        conn.executemany(insert_sql, [entry._asdict() for _, entry in fresh])
        close_intervals(conn, history_table, id_column, closing, pulled_at)
        scope_columns = (scope[0],) if scope else ()
        scope_values = (scope[1],) if scope else ()
        open_intervals(
            conn,
            history_table,
            (id_column, *scope_columns, "hash", "valid_from"),
            [
                (
                    entry_id,
                    *scope_values,
                    entry.hash,
                    entry.valid_from if entry_id in first_time else pulled_at,
                )
                for entry_id, entry in fresh
            ],
        )
    return diff
