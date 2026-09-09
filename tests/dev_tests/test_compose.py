# -----------------------------------------------------------------------------#
# TESTS — pipelines through the same interval rules as protocols
# -----------------------------------------------------------------------------#
"""A pipeline is versioned exactly like a protocol: one active version per guid,
a new hash closes the old interval and opens a new one. The guid is minted once
in the template and is the identity that survives every edit."""

import copy
import sqlite3
from pathlib import Path

import pytest

from compose.compose import (
    AmbiguousProtocolError,
    NodeMismatchError,
    PipelineArtefact,
    PipelineCycleError,
    UnresolvedProtocolError,
    dag_nodes,
    hydrate_pipeline,
    normalize_dag,
    validate_dag,
)
from compose.store import (
    SCHEMA,
    PipelineEntry,
    build_pipeline_entry,
    diff_pipelines,
    format_pipeline_entry,
    get_pipelines,
    write_pipeline,
)
from compose.templates import (
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
# Its graph is written over the by-ID fixture's real protocol ids, so it can be
# hydrated against a store built from that fixture.
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
        rows = query(
            db,
            "SELECT valid_from, deprecated_at FROM pipeline_history "
            "ORDER BY valid_from",
        )
        assert rows == [(CREATED_ON, LATER), (LATER, None)]

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

    def test_a_pipeline_missing_from_the_write_is_deprecated_by_absence(
        self, db, pipeline
    ):
        write_pipeline(
            db,
            format_pipeline_entry([pipeline("a"), pipeline("b")], WRITTEN_AT),
            WRITTEN_AT,
        )
        diff = write_pipeline(db, format_pipeline_entry([pipeline("a")], LATER), LATER)

        assert diff["absent"] == ["b"]
        assert set(live(db)) == {"a"}

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
            row[0]
            for row in query(
                db, "SELECT valid_from FROM pipeline_history ORDER BY valid_from"
            )
        ]
        assert opens == [CREATED_ON, LATER]

    def test_diff_pipelines_reports_without_writing(self, db, pipeline):
        entries = format_pipeline_entry([pipeline()], WRITTEN_AT)
        assert diff_pipelines(db, entries)["new"] == ["abc123"]
        assert query(db, "SELECT COUNT(*) FROM pipeline_history") == [(0,)]


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
            row[0]
            for row in query(
                db, "SELECT hash FROM pipeline_content ORDER BY pipeline_guid DESC"
            )
        ]
        assert [row.hash for row in get_pipelines(db, hashes)] == hashes

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


# -----------------------------------------------------------------------------#
# 8. HYDRATION — the DAG's protocol names become protocol hashes
# -----------------------------------------------------------------------------#
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
# 9. HYDRATION — each node's protocol becomes a protocol hash
# -----------------------------------------------------------------------------#
class TestHydration:
    """`node_hashes` is what makes a pipeline reproduce: `nodes` names protocols
    the way a human writes them, `node_hashes` pins the versions that were active
    when it was hydrated. It is hashed, so re-hydrating onto moved protocols is a
    new pipeline version rather than a silent edit."""

    @pytest.fixture
    def protocols(self, tmp_path, by_id_records):
        """A protocol store holding the whole by-ID fixture, every version live."""
        path = str(tmp_path / "chronos_test.db")
        initialize_db(path, PROTOCOL_SCHEMA)
        artefacts = [
            build_protocol_artefact(copy.deepcopy(r)) for r in by_id_records.values()
        ]
        write_protocols(path, format_protocol_entry(artefacts, WRITTEN_AT), WRITTEN_AT)
        return path

    @pytest.fixture
    def active(self, protocols):
        """protocol_uid -> the hash active in that store."""
        with connect(protocols, read_only=True) as conn:
            return active_hashes(conn, PROTOCOL_HISTORY, PROTOCOL_UID)

    @pytest.fixture
    def two_step(self, pipeline):
        """lyse -> elute, named by bare protocol_id."""

        def _two_step(**nodes) -> PipelineArtefact:
            return pipeline(
                DAG={"lyse": ["elute"], "elute": []},
                nodes={"lyse": "568614", "elute": "400843"} | nodes,
            )

        return _two_step

    def test_each_node_gets_its_protocols_active_hash(
        self, two_step, protocols, active
    ):
        assert hydrate_pipeline(two_step(), protocols).node_hashes == {
            "lyse": active["protocols_io:568614"],
            "elute": active["protocols_io:400843"],
        }

    def test_a_uid_resolves(self, two_step, protocols, active):
        built = hydrate_pipeline(two_step(lyse="protocols_io:568614"), protocols)
        assert built.node_hashes["lyse"] == active["protocols_io:568614"]

    def test_a_guid_resolves(self, two_step, protocols, by_id_records, active):
        guid = by_id_records["baseline"]["guid"]
        built = hydrate_pipeline(two_step(lyse=guid), protocols)
        assert built.node_hashes["lyse"] == active["protocols_io:568614"]

    def test_one_protocol_may_run_at_two_nodes(self, pipeline, protocols, active):
        """The whole reason node ids exist: a repeat is two nodes, one hash."""
        built = hydrate_pipeline(
            pipeline(
                DAG={"wash_1": ["digest"], "digest": ["wash_2"], "wash_2": []},
                nodes={"wash_1": "568614", "digest": "319531", "wash_2": "568614"},
            ),
            protocols,
        )
        assert built.node_hashes["wash_1"] == built.node_hashes["wash_2"]
        assert built.node_hashes["digest"] != built.node_hashes["wash_1"]

    def test_a_branch_survives_hydration(self, pipeline, protocols):
        """A fork and its join must still be readable off the two graphs."""
        built = hydrate_pipeline(
            pipeline(
                DAG={
                    "lyse": ["digest_biomek", "digest_human"],
                    "digest_human": ["elute"],
                    "digest_biomek": ["elute"],
                    "elute": [],
                },
                nodes={
                    "lyse": "568614",
                    "digest_human": "319531",
                    "digest_biomek": "201297",
                    "elute": "400843",
                },
            ),
            protocols,
        )
        assert built.DAG["lyse"] == ["digest_biomek", "digest_human"]
        assert len({built.node_hashes[n] for n in built.DAG["lyse"]}) == 2
        assert built.node_hashes["elute"] not in {
            built.node_hashes[n] for n in built.DAG["lyse"]
        }

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

    def test_an_unknown_protocol_raises_and_names_it(self, two_step, protocols):
        with pytest.raises(UnresolvedProtocolError) as raised:
            hydrate_pipeline(two_step(elute="999999"), protocols)
        assert raised.value.unresolved == ["999999"]

    def test_a_deprecated_protocol_is_not_resolvable(self, two_step, protocols):
        """Hydration pins what is active, so a retired protocol has no hash to give."""
        with connect(protocols) as conn:
            conn.execute(
                "UPDATE protocol_history SET deprecated_at = ? WHERE protocol_uid = ?",
                (LATER, "protocols_io:400843"),
            )
        with pytest.raises(UnresolvedProtocolError, match="400843"):
            hydrate_pipeline(two_step(), protocols)

    def test_an_id_live_on_two_sources_raises(self, two_step, protocols, by_id_records):
        """The collision protocol_uid exists to prevent — never resolved silently."""
        twin = build_protocol_artefact(
            copy.deepcopy(by_id_records["baseline"]), source="zenodo"
        )
        write_protocols(
            protocols, format_protocol_entry([twin], WRITTEN_AT), WRITTEN_AT, "zenodo"
        )
        with pytest.raises(AmbiguousProtocolError) as raised:
            hydrate_pipeline(two_step(), protocols)
        assert len(raised.value.ambiguous["568614"]) == 2

    def test_the_uid_still_resolves_when_the_bare_id_is_ambiguous(
        self, two_step, protocols, by_id_records, active
    ):
        twin = build_protocol_artefact(
            copy.deepcopy(by_id_records["baseline"]), source="zenodo"
        )
        write_protocols(
            protocols, format_protocol_entry([twin], WRITTEN_AT), WRITTEN_AT, "zenodo"
        )
        built = hydrate_pipeline(two_step(lyse="protocols_io:568614"), protocols)
        assert built.node_hashes["lyse"] == active["protocols_io:568614"]

    def test_node_hashes_are_hashed(self, two_step, protocols):
        """The whole point — a pipeline pinning different versions is a new version."""
        dry = build_pipeline_entry(two_step(), WRITTEN_AT)
        wet = build_pipeline_entry(hydrate_pipeline(two_step(), protocols), WRITTEN_AT)
        assert dry.hash != wet.hash

    def test_a_new_protocol_version_rehydrates_to_a_new_pipeline_hash(
        self, two_step, protocols, by_id_records
    ):
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

        assert first.node_hashes != second.node_hashes
        assert (
            build_pipeline_entry(first, WRITTEN_AT).hash
            != build_pipeline_entry(second, WRITTEN_AT).hash
        )

    def test_a_hydrated_template_writes_and_versions(self, db, protocols, tmp_path):
        """End to end: the template's graph, hydrated, stored as a pipeline."""
        source = tmp_path / "pipeline_template.yaml"
        source.write_text(
            FIXTURE_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8"
        )
        built = [
            hydrate_pipeline(p, protocols)
            for p in pipelines_from_template(str(source), mint=True)
        ]

        assert all(p.node_hashes for p in built)
        diff = write_pipeline(db, format_pipeline_entry(built, WRITTEN_AT), WRITTEN_AT)
        assert len(diff["new"]) == 2

    def test_an_unhydrated_template_carries_no_node_hashes(self, tmp_path):
        source = tmp_path / "pipeline_template.yaml"
        source.write_text(
            FIXTURE_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8"
        )
        assert all(
            p.node_hashes == {} for p in pipelines_from_template(str(source), mint=True)
        )

    def test_the_shipped_config_template_is_still_unhydratable(
        self, protocols, tmp_path
    ):
        """Its graph is placeholders — reading it works, hydrating it must not."""
        source = tmp_path / "pg_core_templates.yaml"
        source.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        built = pipelines_from_template(str(source), mint=True)
        with pytest.raises(UnresolvedProtocolError):
            hydrate_pipeline(next(p for p in built if p.DAG), protocols)
