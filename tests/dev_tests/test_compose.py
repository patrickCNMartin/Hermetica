# -----------------------------------------------------------------------------#
# TESTS — pipelines through the same interval rules as protocols
# -----------------------------------------------------------------------------#
"""A pipeline is versioned exactly like a protocol: one active version per guid,
a new hash closes the old interval and opens a new one. The guid is minted once
in the template and is the identity that survives every edit."""

from pathlib import Path

import pytest

from compose.compose import HASH_FIELDS, METADATA_FIELDS, ProtocolPipeline
from compose.store import (
    _CONTENT_COLUMNS,
    DuplicatePipelineGuidError,
    PipelineEntry,
    UnknownPipelineHashError,
    active_pipelines,
    build_pipeline_entry,
    diff_pipelines,
    format_db_entry,
    get_pipelines,
    initialize_pipeline_db,
    pipelines_on_date,
    verify_pipelines,
    write_pipeline,
)
from compose.templates import (
    UnmintedTemplateError,
    mint_template,
    pipelines_from_template,
    read_template,
)
from utils.dates import to_epoch
from utils.hashing import canonical_json
from utils.store import connect

TEMPLATE = Path(__file__).parents[2] / "config" / "pg_core_templates.yaml"

CREATED_ON = to_epoch("2026-08-21")
WRITTEN_AT = to_epoch("2026-09-01")
LATER = to_epoch("2026-09-08")

# One day, two edits: the case a single date cannot disambiguate.
SWAP_DAY = "2026-09-15"
MORNING = to_epoch(SWAP_DAY) + 9 * 3600
EVENING = to_epoch(SWAP_DAY) + 18 * 3600


# -----------------------------------------------------------------------------#
# HELPERS
# -----------------------------------------------------------------------------#
@pytest.fixture
def pipeline():
    def _pipeline(guid: str = "abc123", **overrides) -> ProtocolPipeline:
        fields = {
            "guid": guid,
            "title": "CryPrep_biomek_base",
            "manifest_hash": None,
            "root": None,
            "executor": None,
            "DAG": {"A": ["B", "C"], "B": "D", "C": "D"},
            "created_on": CREATED_ON,
            "creator": "Homunculus Pat",
        }
        return ProtocolPipeline(**{**fields, **overrides})

    return _pipeline


@pytest.fixture
def db(db_path):
    initialize_pipeline_db(db_path)
    return db_path


def query(db: str, sql: str, *params):
    with connect(db, read_only=True) as conn:
        return conn.execute(sql, params).fetchall()


def live(db: str) -> dict[str, str]:
    with connect(db, read_only=True) as conn:
        return active_pipelines(conn)


# -----------------------------------------------------------------------------#
# 1. THE CONTRACT
# -----------------------------------------------------------------------------#
class TestPipelineContract:
    def test_every_hash_field_exists_on_the_dataclass(self, pipeline):
        """DAG_ids used to sit here and did not exist — hashable() raised."""
        assert set(pipeline().hashable()) == set(HASH_FIELDS)

    def test_metadata_is_the_metadata_fields(self, pipeline):
        assert tuple(pipeline().metadata()) == METADATA_FIELDS

    def test_to_dict_carries_both_halves(self, pipeline):
        assert set(pipeline().to_dict()) == set(HASH_FIELDS) | set(METADATA_FIELDS)

    def test_metadata_is_not_hashed(self, pipeline):
        """A different creator is the same pipeline."""
        one = build_pipeline_entry(pipeline(), WRITTEN_AT)
        two = build_pipeline_entry(pipeline(creator="Someone Else"), WRITTEN_AT)
        assert one.hash == two.hash

    def test_a_changed_dag_is_a_new_hash(self, pipeline):
        one = build_pipeline_entry(pipeline(), WRITTEN_AT)
        two = build_pipeline_entry(pipeline(DAG={"A": ["B"]}), WRITTEN_AT)
        assert one.hash != two.hash


# -----------------------------------------------------------------------------#
# 2. THE DATABASE
# -----------------------------------------------------------------------------#
class TestDatabaseBuild:
    CONTENT_COLUMNS = [
        "hash",
        "pipeline_guid",
        "title",
        "manifest_hash",
        "root",
        "executor",
        "DAG",
        "pipeline",
        "created_on",
        "creator",
    ]
    HISTORY_COLUMNS = ["pipeline_guid", "hash", "valid_from", "deprecated_at"]

    def columns_of(self, db, table):
        return [row[1] for row in query(db, f"PRAGMA table_info({table})")]

    def test_tables_are_created(self, db):
        tables = {
            name
            for (name,) in query(
                db, "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"pipeline_content", "pipeline_history"} <= tables

    def test_content_schema(self, db):
        assert self.columns_of(db, "pipeline_content") == self.CONTENT_COLUMNS

    def test_history_schema(self, db):
        assert self.columns_of(db, "pipeline_history") == self.HISTORY_COLUMNS

    def test_content_columns_match_the_table(self, db):
        """Derived from METADATA_FIELDS, never restated."""
        assert list(_CONTENT_COLUMNS) == self.columns_of(db, "pipeline_content")

    def test_entry_carries_the_columns_plus_valid_from(self):
        assert set(PipelineEntry._fields) == set(_CONTENT_COLUMNS) | {"valid_from"}

    def test_initialize_is_idempotent(self, db_path):
        initialize_pipeline_db(db_path)
        initialize_pipeline_db(db_path)
        assert query(db_path, "SELECT COUNT(*) FROM pipeline_content") == [(0,)]


# -----------------------------------------------------------------------------#
# 3. BUILDING AN ENTRY
# -----------------------------------------------------------------------------#
class TestBuildEntry:
    def test_the_blob_is_serialized_once(self, pipeline):
        """The stored bytes are the hashed bytes — they cannot drift apart."""
        built = pipeline()
        entry = build_pipeline_entry(built, WRITTEN_AT)
        assert entry.pipeline.encode("ascii") == canonical_json(built.hashable())

    def test_the_dag_is_stored_as_its_own_canonical_column(self, pipeline):
        """Not the whole hashable blob — the DAG column holds the DAG."""
        entry = build_pipeline_entry(pipeline(), WRITTEN_AT)
        assert entry.DAG == '{"A":["B","C"],"B":"D","C":"D"}'

    def test_valid_from_backdates_to_created_on(self, pipeline):
        entry = build_pipeline_entry(pipeline(), WRITTEN_AT)
        assert entry.valid_from == CREATED_ON

    def test_valid_from_falls_back_to_the_write_time(self, pipeline):
        entry = build_pipeline_entry(pipeline(created_on=None), WRITTEN_AT)
        assert entry.valid_from == WRITTEN_AT

    def test_format_db_entry_builds_one_per_pipeline(self, pipeline):
        entries = format_db_entry([pipeline("a"), pipeline("b")], WRITTEN_AT)
        assert [e.pipeline_guid for e in entries] == ["a", "b"]


# -----------------------------------------------------------------------------#
# 4. WRITING AND VERSIONING
# -----------------------------------------------------------------------------#
class TestWritePipeline:
    def test_a_first_write_is_new(self, db, pipeline):
        diff = write_pipeline(db, format_db_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        assert diff["new"] == ["abc123"]
        assert live(db) == {
            "abc123": query(db, "SELECT hash FROM pipeline_content")[0][0]
        }

    def test_rewriting_the_same_pipeline_opens_no_second_interval(self, db, pipeline):
        entries = format_db_entry([pipeline()], WRITTEN_AT)
        write_pipeline(db, entries, WRITTEN_AT)
        diff = write_pipeline(db, entries, LATER)

        assert diff["unchanged"] == ["abc123"]
        assert query(db, "SELECT COUNT(*) FROM pipeline_history") == [(1,)]
        assert query(db, "SELECT COUNT(*) FROM pipeline_content") == [(1,)]

    def test_a_changed_dag_closes_the_old_interval_and_opens_a_new_one(
        self, db, pipeline
    ):
        write_pipeline(db, format_db_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        edited = pipeline(DAG={"A": ["B"], "B": "D"})
        diff = write_pipeline(db, format_db_entry([edited], LATER), LATER)

        assert diff["changed"] == ["abc123"]
        rows = query(
            db,
            "SELECT valid_from, deprecated_at FROM pipeline_history "
            "ORDER BY valid_from",
        )
        assert rows == [(CREATED_ON, LATER), (LATER, None)]

    def test_only_one_version_is_ever_active(self, db, pipeline):
        write_pipeline(db, format_db_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        write_pipeline(db, format_db_entry([pipeline(DAG={"A": ["B"]})], LATER), LATER)
        assert (
            query(
                db,
                "SELECT pipeline_guid, COUNT(*) FROM pipeline_history "
                "WHERE deprecated_at IS NULL GROUP BY pipeline_guid "
                "HAVING COUNT(*) > 1",
            )
            == []
        )

    def test_a_pipeline_missing_from_the_write_is_deprecated_by_absence(
        self, db, pipeline
    ):
        write_pipeline(
            db, format_db_entry([pipeline("a"), pipeline("b")], WRITTEN_AT), WRITTEN_AT
        )
        diff = write_pipeline(db, format_db_entry([pipeline("a")], LATER), LATER)

        assert diff["absent"] == ["b"]
        assert set(live(db)) == {"a"}

    def test_the_old_blob_survives_deprecation(self, db, pipeline):
        """A pinned pipeline must still resolve after it is superseded."""
        write_pipeline(db, format_db_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        first = query(db, "SELECT hash FROM pipeline_content")[0][0]
        write_pipeline(db, format_db_entry([pipeline(DAG={"A": ["B"]})], LATER), LATER)
        assert get_pipelines(db, [first])[0].hash == first

    def test_only_the_first_ever_version_backdates(self, db, pipeline):
        """created_on says when the pipeline was authored, not this version."""
        write_pipeline(db, format_db_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        write_pipeline(db, format_db_entry([pipeline(DAG={"A": ["B"]})], LATER), LATER)
        opens = [
            row[0]
            for row in query(
                db, "SELECT valid_from FROM pipeline_history ORDER BY valid_from"
            )
        ]
        assert opens == [CREATED_ON, LATER]

    def test_two_versions_of_one_guid_in_a_single_write_is_refused(self, db, pipeline):
        entries = format_db_entry([pipeline(), pipeline(DAG={"A": ["B"]})], WRITTEN_AT)
        with pytest.raises(DuplicatePipelineGuidError):
            write_pipeline(db, entries, WRITTEN_AT)

    def test_diff_pipelines_reports_without_writing(self, db, pipeline):
        entries = format_db_entry([pipeline()], WRITTEN_AT)
        assert diff_pipelines(db, entries)["new"] == ["abc123"]
        assert query(db, "SELECT COUNT(*) FROM pipeline_history") == [(0,)]


# -----------------------------------------------------------------------------#
# 5. READING BACK
# -----------------------------------------------------------------------------#
class TestGetPipelines:
    def test_it_returns_them_in_the_order_asked_for(self, db, pipeline):
        write_pipeline(
            db,
            format_db_entry([pipeline("a"), pipeline("b", DAG={"X": []})], WRITTEN_AT),
        )
        hashes = [
            row[0]
            for row in query(
                db, "SELECT hash FROM pipeline_content ORDER BY pipeline_guid DESC"
            )
        ]
        assert [row.hash for row in get_pipelines(db, hashes)] == hashes

    def test_an_unknown_hash_raises_rather_than_dropping_out(self, db, pipeline):
        write_pipeline(db, format_db_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        known = query(db, "SELECT hash FROM pipeline_content")[0][0]
        with pytest.raises(UnknownPipelineHashError, match="sha256:"):
            get_pipelines(db, [known, "sha256:" + "0" * 64])

    def test_without_the_blob_the_pipeline_column_is_unread(self, db, pipeline):
        write_pipeline(db, format_db_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        known = query(db, "SELECT hash FROM pipeline_content")[0][0]
        assert get_pipelines(db, [known], with_blob=False)[0].pipeline is None

    def test_no_hashes_asks_nothing(self, db):
        assert get_pipelines(db, []) == []


class TestPipelinesOnDate:
    def test_a_version_active_on_that_day_is_returned(self, db, pipeline):
        write_pipeline(db, format_db_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        with connect(db, read_only=True) as conn:
            versions = pipelines_on_date(conn, "2026-09-01")
        assert list(versions) == ["abc123"]

    def test_both_versions_are_returned_for_the_day_they_swapped(self, db, pipeline):
        """Two edits inside one day — the case a single date cannot disambiguate."""
        write_pipeline(db, format_db_entry([pipeline()], MORNING), MORNING)
        write_pipeline(
            db, format_db_entry([pipeline(DAG={"A": ["B"]})], EVENING), EVENING
        )
        with connect(db, read_only=True) as conn:
            versions = pipelines_on_date(conn, SWAP_DAY)
        assert len(versions["abc123"]) == 2

    def test_a_version_closed_at_midnight_is_not_active_that_day(self, db, pipeline):
        """The interval is half-open: closing at 00:00:00 means it held nothing
        on the day that starts there."""
        write_pipeline(db, format_db_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        write_pipeline(db, format_db_entry([pipeline(DAG={"A": ["B"]})], LATER), LATER)
        with connect(db, read_only=True) as conn:
            versions = pipelines_on_date(conn, "2026-09-08")
        assert len(versions["abc123"]) == 1

    def test_a_day_before_anything_existed_is_empty(self, db, pipeline):
        write_pipeline(db, format_db_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        with connect(db, read_only=True) as conn:
            assert pipelines_on_date(conn, "2020-01-01") == {}


# -----------------------------------------------------------------------------#
# 6. INTEGRITY
# -----------------------------------------------------------------------------#
class TestVerifyPipelines:
    def test_an_untouched_store_is_clean(self, db, pipeline):
        write_pipeline(db, format_db_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        assert verify_pipelines(db) == []

    def test_a_tampered_blob_is_named(self, db, pipeline):
        write_pipeline(db, format_db_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        known = query(db, "SELECT hash FROM pipeline_content")[0][0]
        with connect(db) as conn:
            conn.execute(
                "UPDATE pipeline_content SET pipeline = ? WHERE hash = ?",
                ('{"tampered":true}', known),
            )
        assert verify_pipelines(db) == [known]


# -----------------------------------------------------------------------------#
# 7. TEMPLATES
# -----------------------------------------------------------------------------#
class TestTemplates:
    """Every test copies the shipped template into tmp_path first. Minting writes
    a file beside its source, so reading config/ here would litter the repo."""

    @pytest.fixture
    def template(self, tmp_path):
        copy = tmp_path / "pg_core_templates.yaml"
        copy.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        return str(copy)

    def test_the_shipped_template_loads(self, template):
        pipelines = pipelines_from_template(template, mint=True)
        assert len(pipelines) == 7
        assert all(isinstance(p, ProtocolPipeline) for p in pipelines)

    def test_every_pipeline_gets_a_guid(self, template):
        guids = [p.guid for p in pipelines_from_template(template, mint=True)]
        assert all(guids)
        assert len(set(guids)) == 7

    def test_the_block_key_becomes_the_title(self, template):
        titles = {p.title for p in pipelines_from_template(template, mint=True)}
        assert "CryPrep_biomek_base" in titles

    def test_created_on_is_an_epoch_integer(self, template):
        assert all(
            isinstance(p.created_on, int)
            for p in pipelines_from_template(template, mint=True)
        )

    def test_an_unpinned_template_carries_no_manifest(self, template):
        assert all(
            p.manifest_hash is None
            for p in pipelines_from_template(template, mint=True)
        )

    def test_reading_an_unminted_template_refuses_rather_than_minting(self, template):
        """A read never writes. Without this, guids appear as a side effect."""
        with pytest.raises(UnmintedTemplateError, match="CryPrep_biomek_base"):
            pipelines_from_template(template)

    def test_reading_writes_no_file(self, template, tmp_path):
        before = set(tmp_path.iterdir())
        with pytest.raises(UnmintedTemplateError):
            pipelines_from_template(template)
        assert set(tmp_path.iterdir()) == before

    def test_minting_writes_a_twin_and_keeps_the_top_level_keys(self, template):
        result, minted_path = mint_template(template)

        assert minted_path.endswith("_minted.yaml")
        written = read_template(minted_path)
        assert written["creator"] == result["creator"]
        assert set(written["pipelines"]) == set(result["pipelines"])

    def test_a_minted_file_is_not_minted_again(self, template):
        """Re-minting would mint new guids and orphan everything already stored."""
        _, minted_path = mint_template(template)

        first = {p.title: p.guid for p in pipelines_from_template(minted_path)}
        second = {p.title: p.guid for p in pipelines_from_template(minted_path)}
        assert first == second

    def test_minting_leaves_an_existing_guid_alone(self, tmp_path):
        source = tmp_path / "t.yaml"
        source.write_text(
            "creator: me\ncreated_on: '2026-08-21'\n"
            "pipelines:\n"
            "  kept:\n    pipeline_guid: already-here\n    protocol_dag: {}\n"
            "  minted:\n    pipeline_guid: null\n    protocol_dag: {}\n",
            encoding="utf-8",
        )
        template, _ = mint_template(str(source))
        assert template["pipelines"]["kept"]["pipeline_guid"] == "already-here"
        assert template["pipelines"]["minted"]["pipeline_guid"]

    def test_pipelines_from_a_template_write_and_version(self, db, template):
        pipelines = pipelines_from_template(template, mint=True)

        diff = write_pipeline(db, format_db_entry(pipelines, WRITTEN_AT), WRITTEN_AT)
        assert len(diff["new"]) == 7
        assert len(live(db)) == 7

    def test_an_edited_dag_versions_under_the_same_guid(self, db, template):
        """The guid is what survives an edit — that is the point of minting it."""
        pipelines = pipelines_from_template(template, mint=True)
        write_pipeline(db, format_db_entry(pipelines, WRITTEN_AT), WRITTEN_AT)

        edited = [
            ProtocolPipeline(**{**p.to_dict(), "DAG": {"A": ["Z"]}})
            if p.title == "CryPrep_biomek_base"
            else p
            for p in pipelines
        ]
        diff = write_pipeline(db, format_db_entry(edited, LATER), LATER)

        assert len(diff["changed"]) == 1
        assert len(live(db)) == 7
