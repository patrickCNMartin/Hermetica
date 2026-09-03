# -----------------------------------------------------------------------------#
# TESTS — the shared mechanics both stores are built on
# -----------------------------------------------------------------------------#
"""These have no protocol and no pipeline in them on purpose. If a rule here
needs a domain object to state, it belongs in seal or compose, not in utils."""

import pytest

from utils.hashing import as_column, canonical_json, hash_bytes, hash_of
from utils.intervals import diff_entries
from utils.store import connect, fetch_rows, initialize_db, insert_statement

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS thing (
        hash  TEXT PRIMARY KEY,
        name  TEXT NOT NULL,
        blob  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_thing_name ON thing (name)",
)


# -----------------------------------------------------------------------------#
# HASHING
# -----------------------------------------------------------------------------#
class TestHashOf:
    def test_it_is_hash_bytes_over_canonical_json(self):
        payload = {"b": 2, "a": 1}
        assert hash_of(payload) == hash_bytes(canonical_json(payload))

    def test_key_order_does_not_change_the_hash(self):
        assert hash_of({"a": 1, "b": 2}) == hash_of({"b": 2, "a": 1})

    def test_it_carries_the_algorithm_prefix(self):
        assert hash_of({}).startswith("sha256:")

    def test_a_changed_value_changes_the_hash(self):
        assert hash_of({"a": 1}) != hash_of({"a": 2})


class TestAsColumn:
    @pytest.mark.parametrize("value", [None, 1, 1.5, "text"])
    def test_natives_pass_through_untouched(self, value):
        assert as_column(value) is value

    def test_a_dict_becomes_canonical_json_text(self):
        assert as_column({"b": 2, "a": 1}) == '{"a":1,"b":2}'

    def test_a_list_becomes_canonical_json_text(self):
        assert as_column([1, {"b": 2, "a": 1}]) == '[1,{"a":1,"b":2}]'

    def test_the_text_is_ascii_so_it_round_trips_through_sqlite(self):
        assert as_column({"cafe": "café"}).encode("ascii")

    def test_bool_is_not_treated_as_an_int(self):
        """True is an int subclass, so this pins which branch it takes."""
        assert as_column(True) is True


# -----------------------------------------------------------------------------#
# DIFF
# -----------------------------------------------------------------------------#
class TestDiffEntries:
    def test_an_id_with_no_history_is_new(self):
        diff = diff_entries({}, {"1": "sha256:aaa"})
        assert diff["new"] == ["1"]
        assert diff["changed"] == diff["unchanged"] == diff["absent"] == []

    def test_a_different_hash_is_changed(self):
        diff = diff_entries({"1": "sha256:aaa"}, {"1": "sha256:bbb"})
        assert diff["changed"] == ["1"]
        assert diff["new"] == []

    def test_the_same_hash_is_unchanged(self):
        diff = diff_entries({"1": "sha256:aaa"}, {"1": "sha256:aaa"})
        assert diff["unchanged"] == ["1"]
        assert diff["changed"] == []

    def test_an_active_id_missing_from_the_incoming_set_is_absent(self):
        """Deprecate-on-absence rests on this — content addressing cannot see it."""
        diff = diff_entries({"1": "sha256:aaa"}, {})
        assert diff["absent"] == ["1"]

    def test_every_group_is_sorted(self):
        diff = diff_entries(
            {"3": "sha256:x", "9": "sha256:x"},
            {"9": "sha256:y", "3": "sha256:y", "1": "sha256:z", "2": "sha256:z"},
        )
        assert diff["new"] == ["1", "2"]
        assert diff["changed"] == ["3", "9"]

    def test_an_id_lands_in_exactly_one_group(self):
        active = {"1": "sha256:a", "2": "sha256:b", "4": "sha256:d"}
        incoming = {"1": "sha256:a", "2": "sha256:CHANGED", "3": "sha256:c"}
        diff = diff_entries(active, incoming)
        placed = diff["new"] + diff["changed"] + diff["unchanged"] + diff["absent"]
        assert sorted(placed) == ["1", "2", "3", "4"]
        assert len(placed) == len(set(placed))

    def test_two_empty_sets_produce_four_empty_groups(self):
        assert diff_entries({}, {}) == {
            "new": [],
            "changed": [],
            "unchanged": [],
            "absent": [],
        }


# -----------------------------------------------------------------------------#
# SQL BUILDERS
# -----------------------------------------------------------------------------#
class TestInsertStatement:
    def test_it_binds_by_name(self):
        """Named binding is what stops a reordered row writing to wrong columns."""
        assert insert_statement("thing", ("hash", "name")) == (
            "INSERT OR IGNORE INTO thing (hash, name) VALUES (:hash, :name)"
        )

    def test_it_ignores_a_row_already_stored(self, db_path):
        initialize_db(db_path, SCHEMA)
        statement = insert_statement("thing", ("hash", "name", "blob"))
        row = {"hash": "sha256:a", "name": "first", "blob": "{}"}
        with connect(db_path) as conn:
            conn.execute(statement, row)
            conn.execute(statement, {**row, "name": "second"})
        with connect(db_path, read_only=True) as conn:
            assert conn.execute("SELECT name FROM thing").fetchall() == [("first",)]


# -----------------------------------------------------------------------------#
# SCHEMA & READS
# -----------------------------------------------------------------------------#
class TestInitializeDb:
    def test_it_runs_every_statement(self, db_path):
        initialize_db(db_path, SCHEMA)
        with connect(db_path, read_only=True) as conn:
            names = {name for (name,) in conn.execute("SELECT name FROM sqlite_master")}
        assert {"thing", "idx_thing_name"} <= names

    def test_it_is_idempotent(self, db_path):
        initialize_db(db_path, SCHEMA)
        initialize_db(db_path, SCHEMA)
        with connect(db_path, read_only=True) as conn:
            assert conn.execute("SELECT COUNT(*) FROM thing").fetchone() == (0,)


class TestFetchRows:
    @pytest.fixture
    def stocked(self, db_path):
        initialize_db(db_path, SCHEMA)
        statement = insert_statement("thing", ("hash", "name", "blob"))
        with connect(db_path) as conn:
            conn.executemany(
                statement,
                [{"hash": f"sha256:{n}", "name": f"n{n}", "blob": "{}"} for n in "abc"],
            )
        return db_path

    def test_it_keys_on_the_key_column_wherever_it_sits(self, stocked):
        """`name` is column 0 here and the key is column 1 — the index is read,
        not assumed."""
        with connect(stocked, read_only=True) as conn:
            found = fetch_rows(conn, "thing", ("name", "hash"), "hash", ["sha256:a"])
        assert found == {"sha256:a": ("na", "sha256:a")}

    def test_it_returns_the_whole_row_in_column_order(self, stocked):
        with connect(stocked, read_only=True) as conn:
            found = fetch_rows(
                conn, "thing", ("hash", "name", "blob"), "hash", ["sha256:b"]
            )
        assert found["sha256:b"] == ("sha256:b", "nb", "{}")

    def test_no_keys_asks_the_database_nothing(self, stocked):
        with connect(stocked, read_only=True) as conn:
            assert fetch_rows(conn, "thing", ("hash",), "hash", []) == {}

    def test_absences_are_simply_missing_not_raised(self, stocked):
        """Naming an absence is the caller's job — utils owns no error vocabulary."""
        with connect(stocked, read_only=True) as conn:
            found = fetch_rows(
                conn, "thing", ("hash",), "hash", ["sha256:a", "sha256:zzz"]
            )
        assert set(found) == {"sha256:a"}

    def test_a_key_column_outside_the_selection_fails_loudly(self, stocked):
        with connect(stocked, read_only=True) as conn:
            with pytest.raises(ValueError):
                fetch_rows(conn, "thing", ("name",), "hash", ["sha256:a"])
