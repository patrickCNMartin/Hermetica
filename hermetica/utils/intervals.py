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
    absence is computed against every row — safe only while a single writer
    owns the table, since anything it did not pull looks absent.
    """
    where, params = "deprecated_at IS NULL", ()
    if scope:
        where, params = f"{where} AND {scope[0]} = ?", (scope[1],)
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    return {
        row[id_column]: row["hash"]
        for row in cursor.execute(
            f"SELECT {id_column}, hash FROM {table} WHERE {where}", params
        )
    }


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
    return {getattr(row, id_column): row.hash for row in entries}


def diff_entries(
    active: dict[str, str], incoming: dict[str, str]
) -> dict[str, list[str]]:
    """Group ids as new / changed / unchanged / absent.

    Pure: two id -> hash maps in, four sorted id lists out. `absent` is what
    makes deprecate-on-absence possible — content addressing cannot see it.
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
        "absent": sorted(set(active) - set(incoming)),
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
    rows: Iterable[tuple],
) -> None:
    """Open a fresh interval per row. `deprecated_at` is always NULL — that is
    what open means — so the caller names every other column it fills."""
    slots = ", ".join("?" * len(columns))
    conn.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}, deprecated_at) "
        f"VALUES ({slots}, NULL)",
        list(rows),
    )


# -----------------------------------------------------------------------------#
# WRITING VC
# -----------------------------------------------------------------------------#


def version_control_diff(
    db: str,
    history_table: str,
    id_column: str,
    entries: Iterable,
    scope: tuple[str, str] | None = None,
) -> dict[str, list[str]]:
    """Compare a set of entries against the active state, without writing."""
    with connect(db, read_only=True) as conn:
        return diff_entries(
            active_hashes(conn, history_table, id_column, scope),
            incoming_hashes(entries, id_column),
        )


def write_version_control(
    db: str,
    history_table: str,
    id_column: str,
    insert_sql: str,
    entries: list,
    pulled_at: int | None,
    scope: tuple[str, str] | None = None,
) -> dict[str, list[str]]:

    pulled_at = pulled_at if pulled_at is not None else get_timestamp()

    with connect(db) as conn:
        diff = diff_entries(
            active_hashes(conn, history_table, id_column, scope),
            incoming_hashes(entries, id_column),
        )
        first_time = set(diff["new"]) - seen_before(
            conn, history_table, id_column, diff["new"]
        )
        opening = set(diff["new"]) | set(diff["changed"])
        closing = set(diff["changed"]) | set(diff["absent"])
        fresh = [
            (getattr(row, id_column), row)
            for row in entries
            if getattr(row, id_column) in opening
        ]

        # Bound by name, so valid_from riding along unreferenced is harmless.
        conn.executemany(insert_sql, [row._asdict() for _, row in fresh])
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
                    row.hash,
                    row.valid_from if entry_id in first_time else pulled_at,
                )
                for entry_id, row in fresh
            ],
        )
    return diff
