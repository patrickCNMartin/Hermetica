# -----------------------------------------------------------------------------#
# TESTS — pipelines through the same interval rules as protocols
# -----------------------------------------------------------------------------#
"""A pipeline is versioned exactly like a protocol: one active version per guid,
a new hash closes the old interval and opens a new one. The guid is minted once
in the template and is the identity that survives every edit."""

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from compose.compose import (
    NodeMismatchError,
    PipelineArtefact,
    PipelineCycleError,
    UnresolvedProtocolError,
    UnsealedProtocolError,
    check_nodes_sealed,
    dag_nodes,
    hydrate_pipeline,
    normalize_dag,
    validate_dag,
)
from compose.store import (
    SCHEMA,
    InactivePipelineError,
    PipelineEntry,
    build_pipeline_entry,
    format_pipeline_entry,
    get_pipelines,
    retire_pipeline,
    write_pipeline,
)
from compose.templates import (
    guids_for_nodes,
    load_template,
    mint_template,
    pipelines_from_template,
    read_template,
)
from seal.store import SCHEMA as PROTOCOL_SCHEMA
from seal.store import format_protocol_entry, write_protocols
from sources.protocols_io.artefact import build_protocol_artefact
from utils.constants import (
    PIPELINE_CONTENT_FIELDS,
    PIPELINE_GUID,
    PIPELINE_HASH_FIELDS,
    PIPELINE_HISTORY,
    PIPELINE_METADATA_FIELDS,
    PROTOCOL_HISTORY,
    PROTOCOL_UID,
)
from utils.dates import to_epoch
from utils.hashing import canonical_json
from utils.intervals import active_hashes
from utils.store import MissingHash, connect, initialize_db, verify_blobs

TEMPLATE = Path(__file__).parents[2] / "config" / "pg_core_templates.yaml"
# Its nodes name the by-ID fixture's protocols by protocol_uid, so it loads
# against a store built from that fixture.
FIXTURE_TEMPLATE = Path(__file__).parents[1] / "fixtures" / "pipeline_template.yaml"

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
    def _pipeline(guid: str = "abc123", **overrides) -> PipelineArtefact:
        fields = {
            "guid": guid,
            "title": "CryPrep_biomek_base",
            "manifest_hash": None,
            "root": None,
            "DAG": {"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []},
            "nodes": {"A": "A", "B": "B", "C": "C", "D": "D"},
            "node_hashes": {},
            "created_on": CREATED_ON,
            "creator": "Homunculus Pat",
        }
        return PipelineArtefact(**{**fields, **overrides})

    return _pipeline


@pytest.fixture
def db(db_path):
    initialize_db(db_path, SCHEMA)
    return db_path


def query(db: str, sql: str, *params):
    with connect(db, read_only=True) as conn:
        return conn.execute(sql, params).fetchall()


def live(db: str) -> dict[str, str]:
    with connect(db, read_only=True) as conn:
        return active_hashes(conn, PIPELINE_HISTORY, PIPELINE_GUID)


# -----------------------------------------------------------------------------#
# 1. THE CONTRACT
# -----------------------------------------------------------------------------#
class TestPipelineContract:
    def test_every_hash_field_exists_on_the_dataclass(self, pipeline):
        """DAG_ids used to sit here and did not exist — hashable() raised."""
        assert set(pipeline().hashable()) == set(PIPELINE_HASH_FIELDS)

    def test_metadata_is_the_metadata_fields(self, pipeline):
        assert tuple(pipeline().metadata()) == PIPELINE_METADATA_FIELDS

    def test_to_dict_carries_both_halves(self, pipeline):
        assert set(pipeline().to_dict()) == set(PIPELINE_HASH_FIELDS) | set(
            PIPELINE_METADATA_FIELDS
        )

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
        "DAG",
        "nodes",
        "node_hashes",
        "pipeline",
        "created_on",
        "creator",
    ]
    HISTORY_COLUMNS = ["pipeline_guid", "hash", "valid_from", "deprecated_at"]

    def columns_of(self, db, table):
        return [entry[1] for entry in query(db, f"PRAGMA table_info({table})")]

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
        """Derived from PIPELINE_METADATA_FIELDS, never restated."""
        assert list(PIPELINE_CONTENT_FIELDS) == self.columns_of(db, "pipeline_content")

    def test_entry_carries_the_columns_plus_valid_from(self):
        assert set(PipelineEntry._fields) == set(PIPELINE_CONTENT_FIELDS) | {
            "valid_from"
        }

    def test_initialize_is_idempotent(self, db_path):
        initialize_db(db_path, SCHEMA)
        initialize_db(db_path, SCHEMA)
        assert query(db_path, "SELECT COUNT(*) FROM pipeline_content") == [(0,)]


# -----------------------------------------------------------------------------#
# 2b. WRITE PROTECTION
# -----------------------------------------------------------------------------#
class TestPipelineHistoryIsAppendOnly:
    """The same triggers as protocols. compose.db is the file that gains a
    second writer, so these are the ones that stop being optional."""

    @pytest.fixture
    def written(self, db, pipeline):
        write_pipeline(db, format_pipeline_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        return db

    def refuses(self, db: str, sql: str) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            with connect(db) as conn:
                conn.execute(sql)

    def test_history_is_never_deleted(self, written):
        self.refuses(written, "DELETE FROM pipeline_history")

    def test_content_is_never_updated(self, written):
        self.refuses(written, "UPDATE pipeline_content SET title = 'X'")

    def test_a_pipeline_cannot_be_repointed(self, written):
        """Rewriting `hash` would move a version without recording that it moved."""
        self.refuses(
            written,
            "UPDATE pipeline_history SET hash = 'X', deprecated_at = 99",
        )

    def test_two_active_versions_of_one_pipeline_are_refused(self, written):
        """idx_pipeline_history_one_active — the invariant a second writer breaks."""
        guid, digest = query(
            written, "SELECT pipeline_guid, hash FROM pipeline_history"
        )[0]
        with pytest.raises(sqlite3.IntegrityError):
            with connect(written) as conn:
                conn.execute(
                    "INSERT INTO pipeline_history "
                    "(pipeline_guid, hash, valid_from, deprecated_at) "
                    "VALUES (?, ?, 1, NULL)",
                    (guid, digest),
                )


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
        assert entry.DAG == '{"A":["B","C"],"B":["D"],"C":["D"],"D":[]}'

    def test_valid_from_backdates_to_created_on(self, pipeline):
        entry = build_pipeline_entry(pipeline(), WRITTEN_AT)
        assert entry.valid_from == CREATED_ON

    def test_valid_from_falls_back_to_the_write_time(self, pipeline):
        entry = build_pipeline_entry(pipeline(created_on=None), WRITTEN_AT)
        assert entry.valid_from == WRITTEN_AT

    def test_format_db_entry_builds_one_per_pipeline(self, pipeline):
        entries = format_pipeline_entry([pipeline("a"), pipeline("b")], WRITTEN_AT)
        assert [e.pipeline_guid for e in entries] == ["a", "b"]


# -----------------------------------------------------------------------------#
# 4. WRITING AND VERSIONING
# -----------------------------------------------------------------------------#
class TestWritePipeline:
    def test_a_first_write_is_new(self, db, pipeline):
        diff = write_pipeline(
            db, format_pipeline_entry([pipeline()], WRITTEN_AT), WRITTEN_AT
        )
        assert diff["new"] == ["abc123"]
        assert live(db) == {
            "abc123": query(db, "SELECT hash FROM pipeline_content")[0][0]
        }

    def test_rewriting_the_same_pipeline_opens_no_second_interval(self, db, pipeline):
        entries = format_pipeline_entry([pipeline()], WRITTEN_AT)
        write_pipeline(db, entries, WRITTEN_AT)
        diff = write_pipeline(db, entries, LATER)

        assert diff["unchanged"] == ["abc123"]
        assert query(db, "SELECT COUNT(*) FROM pipeline_history") == [(1,)]
        assert query(db, "SELECT COUNT(*) FROM pipeline_content") == [(1,)]

    def test_a_changed_dag_closes_the_old_interval_and_opens_a_new_one(
        self, db, pipeline
    ):
        write_pipeline(db, format_pipeline_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        edited = pipeline(DAG={"A": ["B"], "B": "D"})
        diff = write_pipeline(db, format_pipeline_entry([edited], LATER), LATER)

        assert diff["changed"] == ["abc123"]
        entries = query(
            db,
            "SELECT valid_from, deprecated_at FROM pipeline_history "
            "ORDER BY valid_from",
        )
        assert entries == [(CREATED_ON, LATER), (LATER, None)]

    def test_only_one_version_is_ever_active(self, db, pipeline):
        write_pipeline(db, format_pipeline_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        write_pipeline(
            db, format_pipeline_entry([pipeline(DAG={"A": ["B"]})], LATER), LATER
        )
        assert (
            query(
                db,
                "SELECT pipeline_guid, COUNT(*) FROM pipeline_history "
                "WHERE deprecated_at IS NULL GROUP BY pipeline_guid "
                "HAVING COUNT(*) > 1",
            )
            == []
        )

    def test_a_pipeline_missing_from_the_write_stays_active(self, db, pipeline):
        """Pipelines are saved one at a time. Absence from a write means nothing,
        or every save would retire every other pipeline."""
        write_pipeline(
            db,
            format_pipeline_entry([pipeline("a"), pipeline("b")], WRITTEN_AT),
            WRITTEN_AT,
        )
        edited = pipeline("a", DAG={"A": ["B"], "B": "D"})
        diff = write_pipeline(db, format_pipeline_entry([edited], LATER), LATER)

        assert diff["absent"] == []
        assert set(live(db)) == {"a", "b"}

    def test_diff_pipelines_never_reports_absence(self, db, pipeline):
        write_pipeline(
            db,
            format_pipeline_entry([pipeline("a"), pipeline("b")], WRITTEN_AT),
            WRITTEN_AT,
        )

        diff = write_pipeline(db, format_pipeline_entry([pipeline("a")], LATER), LATER)

        assert diff["absent"] == []
        assert diff["unchanged"] == ["a"]

    def test_the_old_blob_survives_deprecation(self, db, pipeline):
        """A pinned pipeline must still resolve after it is superseded."""
        write_pipeline(db, format_pipeline_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        first = query(db, "SELECT hash FROM pipeline_content")[0][0]
        write_pipeline(
            db, format_pipeline_entry([pipeline(DAG={"A": ["B"]})], LATER), LATER
        )
        assert get_pipelines(db, [first])[0].hash == first

    def test_only_the_first_ever_version_backdates(self, db, pipeline):
        """created_on says when the pipeline was authored, not this version."""
        write_pipeline(db, format_pipeline_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        write_pipeline(
            db, format_pipeline_entry([pipeline(DAG={"A": ["B"]})], LATER), LATER
        )
        opens = [
            entry[0]
            for entry in query(
                db, "SELECT valid_from FROM pipeline_history ORDER BY valid_from"
            )
        ]
        assert opens == [CREATED_ON, LATER]


class TestRetirePipeline:
    def write(self, db, pipeline, *guids):
        entries = format_pipeline_entry([pipeline(g) for g in guids], WRITTEN_AT)
        write_pipeline(db, entries, WRITTEN_AT)

    def test_it_closes_only_that_pipeline(self, db, pipeline):
        self.write(db, pipeline, "a", "b")

        retire_pipeline(db, "a", LATER)

        assert set(live(db)) == {"b"}
        assert query(
            db, "SELECT deprecated_at FROM pipeline_history WHERE pipeline_guid = 'a'"
        ) == [(LATER,)]

    def test_it_returns_the_hash_it_retired(self, db, pipeline):
        self.write(db, pipeline, "a")
        active = live(db)["a"]

        assert retire_pipeline(db, "a", LATER) == active

    def test_the_content_survives_retirement(self, db, pipeline):
        """A lock pinned to it must still resolve."""
        self.write(db, pipeline, "a")
        digest = retire_pipeline(db, "a", LATER)

        assert get_pipelines(db, [digest])[0].hash == digest

    def test_an_unknown_pipeline_is_refused(self, db):
        with pytest.raises(InactivePipelineError) as error:
            retire_pipeline(db, "nope", LATER)

        assert error.value.guid == "nope"

    def test_retiring_twice_is_refused(self, db, pipeline):
        """A second retire would otherwise look like success on nothing."""
        self.write(db, pipeline, "a")
        retire_pipeline(db, "a", LATER)

        with pytest.raises(InactivePipelineError):
            retire_pipeline(db, "a", LATER)

    def test_saving_it_again_opens_a_new_interval(self, db, pipeline):
        """Never reopens the closed one — history stays append-only."""
        self.write(db, pipeline, "a")
        retire_pipeline(db, "a", LATER)

        diff = write_pipeline(db, format_pipeline_entry([pipeline("a")], LATER), LATER)

        assert diff["new"] == ["a"]
        assert query(
            db,
            "SELECT valid_from, deprecated_at FROM pipeline_history ORDER BY rowid",
        ) == [(CREATED_ON, LATER), (LATER, None)]


# -----------------------------------------------------------------------------#
# 5. READING BACK
# -----------------------------------------------------------------------------#
class TestGetPipelines:
    def test_it_returns_them_in_the_order_asked_for(self, db, pipeline):
        write_pipeline(
            db,
            format_pipeline_entry(
                [pipeline("a"), pipeline("b", DAG={"X": []})], WRITTEN_AT
            ),
        )
        hashes = [
            entry[0]
            for entry in query(
                db, "SELECT hash FROM pipeline_content ORDER BY pipeline_guid DESC"
            )
        ]
        assert [entry.hash for entry in get_pipelines(db, hashes)] == hashes

    def test_an_unknown_hash_raises_rather_than_dropping_out(self, db, pipeline):
        write_pipeline(db, format_pipeline_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        known = query(db, "SELECT hash FROM pipeline_content")[0][0]
        with pytest.raises(MissingHash, match="sha256:"):
            get_pipelines(db, [known, "sha256:" + "0" * 64])

    def test_without_the_blob_the_pipeline_column_is_unread(self, db, pipeline):
        write_pipeline(db, format_pipeline_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        known = query(db, "SELECT hash FROM pipeline_content")[0][0]
        assert get_pipelines(db, [known], with_blob=False)[0].pipeline is None

    def test_no_hashes_asks_nothing(self, db):
        assert get_pipelines(db, []) == []


# -----------------------------------------------------------------------------#
# 6. INTEGRITY
# -----------------------------------------------------------------------------#
class TestVerifyPipelines:
    def test_an_untouched_store_is_clean(self, db, pipeline):
        write_pipeline(db, format_pipeline_entry([pipeline()], WRITTEN_AT), WRITTEN_AT)
        assert verify_blobs(db, "pipeline_content", "hash", "pipeline") == []

    def test_a_blob_that_does_not_match_its_key_is_named(self, db):
        """Built by INSERT: content is immutable in the schema, and disk
        corruption never arrives through SQL anyway."""
        liar = "sha256:" + "0" * 64
        with connect(db) as conn:
            conn.execute(
                "INSERT INTO pipeline_content (hash, pipeline_guid, title, DAG, "
                "nodes, node_hashes, pipeline) "
                "VALUES (?, 'g1', 'T', '{}', '{}', '{}', ?)",
                (liar, '{"tampered":true}'),
            )
        assert verify_blobs(db, "pipeline_content", "hash", "pipeline") == [liar]


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
        assert all(isinstance(p, PipelineArtefact) for p in pipelines)

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

        diff = write_pipeline(
            db, format_pipeline_entry(pipelines, WRITTEN_AT), WRITTEN_AT
        )
        assert len(diff["new"]) == 7
        assert len(live(db)) == 7

    def test_an_edited_dag_versions_under_the_same_guid(self, db, template):
        """The guid is what survives an edit — that is the point of minting it."""
        pipelines = pipelines_from_template(template, mint=True)
        write_pipeline(db, format_pipeline_entry(pipelines, WRITTEN_AT), WRITTEN_AT)

        edited = [
            PipelineArtefact(**{**p.to_dict(), "DAG": {"A": ["Z"]}})
            if p.title == "CryPrep_biomek_base"
            else p
            for p in pipelines
        ]
        diff = write_pipeline(db, format_pipeline_entry(edited, LATER), LATER)

        assert len(diff["changed"]) == 1
        assert len(live(db)) == 7


# ---------------------------------------------------------------------------#
# 8. THE GRAPH ITSELF
# ---------------------------------------------------------------------------#
class TestGraphShape:
    """The DAG is hashed, so two ways of writing one graph must not be two
    versions — and a graph that cannot run must not get a hash at all."""

    def test_it_names_every_node_once(self):
        """Keys and successors alike, a bare string and a list read the same."""
        assert dag_nodes({"a": ["b", "c"], "b": "d", "c": "d"}) == ["a", "b", "c", "d"]

    def test_a_lone_successor_becomes_a_one_item_list(self):
        assert normalize_dag({"a": "b"}) == {"a": ["b"]}

    def test_successors_are_sorted(self):
        """A fork is parallel and conditional, so the order it was written in is
        not information and must not reach the hash."""
        assert normalize_dag({"a": ["c", "b"]}) == normalize_dag({"a": ["b", "c"]})

    def test_a_reordered_fork_is_the_same_pipeline(self, pipeline):
        one = pipeline(DAG=normalize_dag({"A": ["B", "C"], "B": [], "C": []}))
        two = pipeline(DAG=normalize_dag({"A": ["C", "B"], "B": [], "C": []}))
        assert (
            build_pipeline_entry(one, WRITTEN_AT).hash
            == build_pipeline_entry(two, WRITTEN_AT).hash
        )

    def test_a_node_running_no_protocol_raises(self):
        with pytest.raises(NodeMismatchError) as raised:
            validate_dag({"a": ["ghost"]}, {"a": "568614"})
        assert raised.value.unnamed == ["ghost"]

    def test_a_protocol_on_no_node_raises(self):
        with pytest.raises(NodeMismatchError) as raised:
            validate_dag({"a": []}, {"a": "568614", "stray": "400843"})
        assert raised.value.orphaned == ["stray"]

    def test_a_cycle_raises_and_names_it(self):
        with pytest.raises(PipelineCycleError) as raised:
            validate_dag(
                {"a": ["b"], "b": ["c"], "c": ["a"]},
                {"a": "1", "b": "2", "c": "3"},
            )
        assert set(raised.value.cycle) >= {"a", "b", "c"}

    def test_a_self_loop_is_a_cycle(self):
        with pytest.raises(PipelineCycleError):
            validate_dag({"a": ["a"]}, {"a": "568614"})

    def test_a_diamond_is_not_a_cycle(self):
        """Two branches converging is the shape this must not reject."""
        validate_dag(
            {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []},
            {"a": "1", "b": "2", "c": "3", "d": "4"},
        )

    def test_an_empty_graph_is_valid(self):
        validate_dag({}, {})


# -----------------------------------------------------------------------------#
# 9. PROTOCOLS BY GUID — sealed on save, pinned on lock
# -----------------------------------------------------------------------------#
@pytest.fixture
def protocols(tmp_path, by_id_records):
    """A protocol store holding the whole by-ID fixture, every version live."""
    path = str(tmp_path / "chronos_test.db")
    initialize_db(path, PROTOCOL_SCHEMA)
    artefacts = [
        build_protocol_artefact(copy.deepcopy(r)) for r in by_id_records.values()
    ]
    write_protocols(path, format_protocol_entry(artefacts, WRITTEN_AT), WRITTEN_AT)
    return path


@pytest.fixture
def active(protocols):
    """protocol_uid -> the hash active in that store."""
    with connect(protocols, read_only=True) as conn:
        return active_hashes(conn, PROTOCOL_HISTORY, PROTOCOL_UID)


@pytest.fixture
def guid(by_id_records):
    """protocol_id -> its protocol_guid, so tests read by the id a person knows."""
    by_id = {str(r["id"]): r["guid"] for r in by_id_records.values()}
    return by_id.__getitem__


@pytest.fixture
def two_step(pipeline, guid):
    """lyse -> elute, each node naming its protocol by guid."""

    def _two_step(**nodes) -> PipelineArtefact:
        return pipeline(
            DAG={"lyse": ["elute"], "elute": []},
            nodes={"lyse": guid("568614"), "elute": guid("400843")} | nodes,
        )

    return _two_step


def retire_protocol(protocols, uid):
    """Close a protocol's interval — the only legal update the triggers allow."""
    with connect(protocols) as conn:
        conn.execute(
            "UPDATE protocol_history SET deprecated_at = ? "
            "WHERE protocol_uid = ? AND deprecated_at IS NULL",
            (LATER, uid),
        )


class TestSealedCheck:
    """Saving a template: every guid must name a protocol the store has held.
    Whether that protocol is still active is the lock's question, not the save's."""

    def test_known_guids_pass(self, two_step, protocols):
        check_nodes_sealed(two_step(), protocols)

    def test_an_inactive_protocol_passes(self, two_step, protocols):
        """A template outlives the protocol versions it was drawn with."""
        retire_protocol(protocols, "protocols_io:400843")
        check_nodes_sealed(two_step(), protocols)

    def test_a_guid_the_store_never_held_is_refused_by_node(self, two_step, protocols):
        with pytest.raises(UnsealedProtocolError) as raised:
            check_nodes_sealed(two_step(elute="NOPE"), protocols)
        assert raised.value.nodes == {"elute": "NOPE"}

    def test_a_bare_protocol_id_is_not_a_guid(self, two_step, protocols):
        with pytest.raises(UnsealedProtocolError):
            check_nodes_sealed(two_step(lyse="568614"), protocols)


class TestHydration:
    """Hydration is the lock step: each node's guid is pinned to the hash active
    right now. `node_hashes` is hashed, so the pinned copy is its own content
    address — which is why it lives in a lock and never in the template store."""

    def test_each_node_gets_its_protocols_active_hash(
        self, two_step, protocols, active
    ):
        assert hydrate_pipeline(two_step(), protocols).node_hashes == {
            "lyse": active["protocols_io:568614"],
            "elute": active["protocols_io:400843"],
        }

    def test_one_protocol_may_run_at_two_nodes(self, pipeline, protocols, guid):
        """The whole reason node ids exist: a repeat is two nodes, one hash."""
        built = hydrate_pipeline(
            pipeline(
                DAG={"wash_1": ["digest"], "digest": ["wash_2"], "wash_2": []},
                nodes={
                    "wash_1": guid("568614"),
                    "digest": guid("319531"),
                    "wash_2": guid("568614"),
                },
            ),
            protocols,
        )
        assert built.node_hashes["wash_1"] == built.node_hashes["wash_2"]
        assert built.node_hashes["digest"] != built.node_hashes["wash_1"]

    def test_an_empty_pipeline_hydrates_to_nothing(self, pipeline, protocols):
        built = hydrate_pipeline(pipeline(DAG={}, nodes={}), protocols)
        assert built.node_hashes == {}

    def test_the_readable_graphs_are_left_alone(self, two_step, protocols):
        """`DAG` and `nodes` are what a person edits; they must survive."""
        before = two_step()
        after = hydrate_pipeline(before, protocols)
        assert (after.DAG, after.nodes) == (before.DAG, before.nodes)

    def test_a_cycle_is_refused_before_the_database_is_touched(self, pipeline):
        with pytest.raises(PipelineCycleError):
            hydrate_pipeline(
                pipeline(DAG={"a": ["b"], "b": ["a"]}, nodes={"a": "1", "b": "2"}),
                "no-such.db",
            )

    def test_an_inactive_protocol_is_refused_by_node(self, two_step, protocols, guid):
        """A lock is a guarantee: nothing retired is pinned, and the error says
        which step to swap out."""
        retire_protocol(protocols, "protocols_io:400843")
        with pytest.raises(UnresolvedProtocolError) as raised:
            hydrate_pipeline(two_step(), protocols)
        assert raised.value.nodes == {"elute": guid("400843")}

    def test_an_unknown_guid_is_refused_by_node(self, two_step, protocols):
        with pytest.raises(UnresolvedProtocolError) as raised:
            hydrate_pipeline(two_step(elute="NOPE"), protocols)
        assert raised.value.nodes == {"elute": "NOPE"}

    def test_a_new_protocol_version_pins_the_new_hash(
        self, two_step, protocols, by_id_records
    ):
        """The template does not change when a protocol moves on; the pin does."""
        first = hydrate_pipeline(two_step(), protocols)

        records = copy.deepcopy(by_id_records)
        records["baseline"]["title"] = "Filtrate vortex resuspend protocol, revised"
        write_protocols(
            protocols,
            format_protocol_entry(
                [build_protocol_artefact(r) for r in records.values()], LATER
            ),
            LATER,
            "protocols_io",
        )
        second = hydrate_pipeline(two_step(), protocols)

        assert first.node_hashes["lyse"] != second.node_hashes["lyse"]
        assert (
            build_pipeline_entry(two_step(), WRITTEN_AT).hash
            == build_pipeline_entry(two_step(), LATER).hash
        )

    def test_node_hashes_are_hashed(self, two_step, protocols):
        """So a pinned copy never collides with the template it came from."""
        template = build_pipeline_entry(two_step(), WRITTEN_AT)
        pinned = build_pipeline_entry(
            hydrate_pipeline(two_step(), protocols), WRITTEN_AT
        )
        assert template.hash != pinned.hash


# -----------------------------------------------------------------------------#
# 10. BOOTSTRAP — a hand-written template into the store
# -----------------------------------------------------------------------------#
class TestLoadTemplate:
    """The config template bootstraps pipelines the team built in the old tool.
    People write protocol_uids; the store holds guids only."""

    @pytest.fixture
    def source(self, tmp_path):
        path = tmp_path / "pipeline_template.yaml"
        path.write_text(FIXTURE_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        return str(path)

    def test_uids_are_stored_as_guids(self, db, protocols, source, guid):
        diff = load_template(source, db, protocols, WRITTEN_AT, mint=True)

        assert len(diff["new"]) == 2
        stored = [
            json.loads(nodes)
            for (nodes,) in query(db, "SELECT nodes FROM pipeline_content")
        ]
        assert {"lyse": guid("568614"), "elute": guid("400843")} in stored

    def test_a_guid_is_kept_as_written(self, pipeline, protocols, guid):
        built = guids_for_nodes(
            pipeline(nodes={"A": guid("568614")}, DAG={"A": []}), protocols
        )
        assert built.nodes == {"A": guid("568614")}

    def test_an_inactive_protocol_still_loads(self, db, protocols, source):
        retire_protocol(protocols, "protocols_io:400843")
        assert (
            len(load_template(source, db, protocols, WRITTEN_AT, mint=True)["new"]) == 2
        )

    def test_a_bare_id_is_refused_not_guessed(self, pipeline, protocols):
        """A bare id collides across sources — the uid exists to prevent that."""
        with pytest.raises(UnsealedProtocolError) as raised:
            guids_for_nodes(pipeline(nodes={"A": "568614"}, DAG={"A": []}), protocols)
        assert raised.value.nodes == {"A": "568614"}

    def test_one_bad_name_loads_nothing(self, db, protocols, source):
        text = Path(source).read_text(encoding="utf-8")
        Path(source).write_text(
            text.replace("protocols_io:400843", "protocols_io:999999", 1),
            encoding="utf-8",
        )
        with pytest.raises(UnsealedProtocolError):
            load_template(source, db, protocols, WRITTEN_AT, mint=True)
        assert live(db) == {}

    def test_a_loaded_template_carries_no_hashes(self, db, protocols, source):
        load_template(source, db, protocols, WRITTEN_AT, mint=True)
        assert query(
            db, "SELECT DISTINCT manifest_hash, node_hashes FROM pipeline_content"
        ) == [(None, "{}")]

    def test_the_shipped_config_template_does_not_load_yet(
        self, db, protocols, tmp_path
    ):
        """Its graphs are placeholders — reading it works, loading it must not."""
        source = tmp_path / "pg_core_templates.yaml"
        source.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        with pytest.raises(UnsealedProtocolError):
            load_template(str(source), db, protocols, WRITTEN_AT, mint=True)
        assert live(db) == {}
