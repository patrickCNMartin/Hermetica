# -----------------------------------------------------------------------------#
# TESTS — the query and compose ports
# -----------------------------------------------------------------------------#
"""The ports are what outside tools see, so what they promise is tested here
without HTTP: stable ids in and out, the query port never writes, a template is
a DAG of guids and never a hash, and versions are pinned only by a lock."""

import copy
import os
import stat

import pytest

from api.contract import InvalidRequestError
from api.pipelines import (
    UnknownPipelineError,
    export_lock,
    get_pipeline,
    list_pipelines,
    pipeline_versions,
    retire,
    save_pipeline,
)
from api.query import (
    UnknownProtocolError,
    get_protocol,
    list_protocols,
    protocol_versions,
)
from compose.compose import (
    PipelineCycleError,
    UnresolvedProtocolError,
    UnsealedProtocolError,
)
from compose.store import SCHEMA as PIPELINE_SCHEMA
from compose.store import InactivePipelineError
from seal.store import SCHEMA as PROTOCOL_SCHEMA
from seal.store import format_protocol_entry, write_protocols
from sources.protocols_io.artefact import build_protocol_artefact
from utils.dates import as_iso, to_epoch
from utils.store import MissingHash, connect, initialize_db

WRITTEN_AT = to_epoch("2026-09-01")
LATER = to_epoch("2026-09-08")
LATEST = to_epoch("2026-09-15")

# protocol_guids from the by-ID fixture: 568614 and 400843.
LYSE = "17B80155EBEA1E31DA8815594973DB85"
ELUTE = "5795146727160E82C86C56274B520277"


# -----------------------------------------------------------------------------#
# STORES
# -----------------------------------------------------------------------------#
@pytest.fixture
def artefacts(by_id_records):
    return [build_protocol_artefact(copy.deepcopy(r)) for r in by_id_records.values()]


@pytest.fixture
def protocols(tmp_path, artefacts):
    """chronos.db holding the whole by-ID fixture, every version live."""
    path = str(tmp_path / "chronos.db")
    initialize_db(path, PROTOCOL_SCHEMA)
    write_protocols(path, format_protocol_entry(artefacts, WRITTEN_AT), WRITTEN_AT)
    return path


@pytest.fixture
def pipelines(tmp_path):
    path = str(tmp_path / "compose.db")
    initialize_db(path, PIPELINE_SCHEMA)
    return path


def body(**overrides) -> dict:
    """lyse -> elute, each node naming its protocol by guid."""
    return {
        "title": "lyse_and_elute",
        "dag": {"lyse": ["elute"], "elute": []},
        "nodes": {"lyse": LYSE, "elute": ELUTE},
        "creator": {"name": "Ada"},
    } | overrides


def retire_protocol(protocols, uid):
    """Close a protocol's interval, as a pull that no longer sees it would."""
    with connect(protocols) as conn:
        conn.execute(
            "UPDATE protocol_history SET deprecated_at = ? "
            "WHERE protocol_uid = ? AND deprecated_at IS NULL",
            (LATER, uid),
        )


# -----------------------------------------------------------------------------#
# 1. THE QUERY PORT
# -----------------------------------------------------------------------------#
class TestListProtocols:
    def test_one_entry_per_active_protocol(self, protocols, artefacts):
        listed = list_protocols(protocols)

        assert [p["protocol_uid"] for p in listed] == sorted(
            f"{a.source}:{a.id}" for a in artefacts
        )

    def test_no_body_is_sent(self, protocols):
        """A list is for choosing; the body is fetched by hash."""
        assert all("protocol" not in p for p in list_protocols(protocols))

    def test_times_are_iso_at_the_boundary(self, protocols):
        assert all(
            p["valid_from"].endswith("+00:00") for p in list_protocols(protocols)
        )

    def test_a_deprecated_protocol_is_not_listed(self, protocols, artefacts):
        kept = [a for a in artefacts if a.id != 568614]
        write_protocols(
            protocols, format_protocol_entry(kept, LATER), LATER, "protocols_io"
        )

        uids = {p["protocol_uid"] for p in list_protocols(protocols)}
        assert "protocols_io:568614" not in uids


class TestProtocolVersions:
    def test_every_interval_oldest_first(self, protocols, artefacts):
        kept = [a for a in artefacts if a.id != 568614]
        write_protocols(
            protocols, format_protocol_entry(kept, LATER), LATER, "protocols_io"
        )

        versions = protocol_versions(protocols, "protocols_io:568614")["versions"]

        assert len(versions) == 1
        assert versions[0]["deprecated_at"] == as_iso(LATER)

    def test_an_active_version_has_no_end(self, protocols):
        versions = protocol_versions(protocols, "protocols_io:400843")["versions"]
        assert versions[-1]["deprecated_at"] is None

    def test_an_unknown_uid_is_refused(self, protocols):
        with pytest.raises(UnknownProtocolError) as raised:
            protocol_versions(protocols, "protocols_io:1")
        assert raised.value.protocol_uid == "protocols_io:1"


class TestGetProtocol:
    def test_it_carries_the_body(self, protocols):
        digest = list_protocols(protocols)[0]["hash"]

        found = get_protocol(protocols, digest)

        assert found["hash"] == digest
        assert isinstance(found["protocol"], dict)

    def test_an_unknown_hash_is_refused(self, protocols):
        with pytest.raises(MissingHash):
            get_protocol(protocols, "sha256:nope")


class TestTheQueryPortNeverWrites:
    def test_every_read_works_on_a_file_nobody_may_write(self, protocols):
        """If a write ever creeps into the query port, this is where it fails."""
        os.chmod(protocols, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        try:
            listed = list_protocols(protocols)
            uid, digest = listed[0]["protocol_uid"], listed[0]["hash"]
            protocol_versions(protocols, uid)
            get_protocol(protocols, digest)
        finally:
            os.chmod(protocols, stat.S_IRUSR | stat.S_IWUSR)


# -----------------------------------------------------------------------------#
# 2. THE COMPOSE PORT — saving a template
# -----------------------------------------------------------------------------#
def save(pipelines, protocols, payload=None, guid=None, at=WRITTEN_AT):
    return save_pipeline(pipelines, protocols, payload or body(), guid, saved_at=at)


class TestSavePipeline:
    def test_a_first_save_mints_a_guid(self, pipelines, protocols):
        saved = save(pipelines, protocols)

        assert saved["status"] == "new"
        assert len(saved["pipeline_guid"]) == 32

    def test_a_template_is_stored_without_hashes(self, pipelines, protocols):
        """The DAG and the guids — which versions run is the lock's business."""
        save(pipelines, protocols)
        with connect(pipelines, read_only=True) as conn:
            stored = conn.execute(
                "SELECT manifest_hash, node_hashes FROM pipeline_content"
            ).fetchall()
        assert stored == [(None, "{}")]

    def test_a_protocol_moving_on_does_not_version_the_template(
        self, pipelines, protocols, artefacts
    ):
        first = save(pipelines, protocols)
        revised = [
            a if a.id != 568614 else type(a)(**{**a.__dict__, "title": "revised"})
            for a in artefacts
        ]
        write_protocols(
            protocols, format_protocol_entry(revised, LATER), LATER, "protocols_io"
        )

        again = save(pipelines, protocols, guid=first["pipeline_guid"], at=LATEST)

        assert again["status"] == "unchanged"

    def test_saving_the_same_body_again_is_unchanged(self, pipelines, protocols):
        first = save(pipelines, protocols)

        again = save(pipelines, protocols, guid=first["pipeline_guid"], at=LATER)

        assert again["status"] == "unchanged"
        assert again["hash"] == first["hash"]

    def test_a_changed_dag_is_a_new_version_under_the_same_guid(
        self, pipelines, protocols
    ):
        guid = save(pipelines, protocols)["pipeline_guid"]

        edited = body(dag={"lyse": [], "elute": ["lyse"]})
        again = save(pipelines, protocols, edited, guid, at=LATER)

        assert again["status"] == "changed"
        assert [p["pipeline_guid"] for p in list_pipelines(pipelines, protocols)] == [
            guid
        ]

    def test_a_swapped_protocol_is_a_new_version(self, pipelines, protocols):
        """The guids used are part of the shape — swap one, and it has changed."""
        guid = save(pipelines, protocols)["pipeline_guid"]

        swapped = body(nodes={"lyse": LYSE, "elute": LYSE})
        assert save(pipelines, protocols, swapped, guid, LATER)["status"] == "changed"

    def test_an_edit_keeps_when_it_was_authored(self, pipelines, protocols):
        guid = save(pipelines, protocols)["pipeline_guid"]
        save(
            pipelines, protocols, body(dag={"lyse": [], "elute": ["lyse"]}), guid, LATER
        )

        found = get_pipeline(pipelines, protocols, guid)
        assert found["created_on"] == as_iso(WRITTEN_AT)

    def test_saving_one_leaves_the_others_active(self, pipelines, protocols):
        save(pipelines, protocols, body(title="one"))
        save(pipelines, protocols, body(title="two"), at=LATER)

        titles = sorted(p["title"] for p in list_pipelines(pipelines, protocols))
        assert titles == ["one", "two"]

    def test_a_guid_the_store_never_minted_is_refused(self, pipelines, protocols):
        with pytest.raises(UnknownPipelineError) as raised:
            save(pipelines, protocols, guid="f" * 32)
        assert raised.value.guid == "f" * 32
        assert list_pipelines(pipelines, protocols) == []

    def test_a_retired_template_can_be_saved_again(self, pipelines, protocols):
        guid = save(pipelines, protocols)["pipeline_guid"]
        retire(pipelines, guid, LATER)

        again = save(pipelines, protocols, guid=guid, at=LATEST)

        assert again["status"] == "new"
        assert get_pipeline(pipelines, protocols, guid)["valid_from"] == as_iso(LATEST)

    def test_an_inactive_protocol_may_be_saved(self, pipelines, protocols):
        """The user fixes it later by swapping the node; saving must not block."""
        retire_protocol(protocols, "protocols_io:400843")
        assert save(pipelines, protocols)["status"] == "new"


class TestCreator:
    @pytest.mark.parametrize("creator", ["Ada", {"name": "Ada"}, None])
    def test_it_reads_back_as_it_was_sent(self, pipelines, protocols, creator):
        """A template's `creator: "Homunculus Pat"` is a plain string."""
        saved = save(pipelines, protocols, body(creator=creator))

        found = get_pipeline(pipelines, protocols, saved["pipeline_guid"])
        assert found["creator"] == creator
        export_lock(pipelines, protocols, saved["pipeline_guid"], LATER)


class TestSaveRefuses:
    """Nothing is written by a save that fails."""

    @pytest.mark.parametrize(
        "bad, problem",
        [
            ({"title": ""}, "`title`"),
            ({"dag": ["lyse"]}, "`dag`"),
            ({"dag": {"lyse": 3}}, "`dag`"),
            ({"nodes": {"lyse": None}}, "`nodes`"),
            ({"nodes": {"lyse": 568614}}, "`nodes`"),
            ({"nodes": {"lyse": ""}}, "`nodes`"),
            ({"root": 5}, "`root`"),
            ({"creator": 5}, "`creator`"),
        ],
    )
    def test_a_malformed_body_names_the_problem(
        self, pipelines, protocols, bad, problem
    ):
        with pytest.raises(InvalidRequestError) as raised:
            save(pipelines, protocols, body(**bad))
        assert any(problem in p for p in raised.value.problems)
        assert list_pipelines(pipelines, protocols) == []

    def test_every_problem_is_listed_at_once(self, pipelines, protocols):
        with pytest.raises(InvalidRequestError) as raised:
            save(pipelines, protocols, {"dag": 1})
        assert len(raised.value.problems) == 3

    def test_a_body_that_is_not_an_object_is_refused(self, pipelines, protocols):
        with pytest.raises(InvalidRequestError):
            save(pipelines, protocols, ["nope"])

    def test_a_cycle_is_refused(self, pipelines, protocols):
        cyclic = body(dag={"lyse": ["elute"], "elute": ["lyse"]})
        with pytest.raises(PipelineCycleError):
            save(pipelines, protocols, cyclic)
        assert list_pipelines(pipelines, protocols) == []

    def test_a_guid_no_pull_ever_sealed_is_refused_by_node(self, pipelines, protocols):
        with pytest.raises(UnsealedProtocolError) as raised:
            save(pipelines, protocols, body(nodes={"lyse": LYSE, "elute": "NOPE"}))
        assert raised.value.nodes == {"elute": "NOPE"}
        assert list_pipelines(pipelines, protocols) == []

    def test_a_bare_protocol_id_is_refused(self, pipelines, protocols):
        """Backend transactions are guid only; the id is for display."""
        with pytest.raises(UnsealedProtocolError):
            save(pipelines, protocols, body(nodes={"lyse": "568614", "elute": ELUTE}))


# -----------------------------------------------------------------------------#
# 3. THE COMPOSE PORT — reading, retiring
# -----------------------------------------------------------------------------#
class TestReadTemplate:
    def test_each_node_carries_its_protocols_current_state(self, pipelines, protocols):
        guid = save(pipelines, protocols)["pipeline_guid"]
        active = {p["protocol_guid"]: p for p in list_protocols(protocols)}

        lyse = get_pipeline(pipelines, protocols, guid)["nodes"]["lyse"]

        assert lyse == {
            "protocol_guid": LYSE,
            "status": "active",
            "protocol_uid": "protocols_io:568614",
            "title": active[LYSE]["title"],
            "hash": active[LYSE]["hash"],
        }

    def test_an_inactive_protocol_is_sent_and_marked(self, pipelines, protocols):
        """Still in the DAG, so the UI can draw it and the user can swap it."""
        guid = save(pipelines, protocols)["pipeline_guid"]
        retire_protocol(protocols, "protocols_io:400843")

        nodes = get_pipeline(pipelines, protocols, guid)["nodes"]

        assert nodes["elute"]["status"] == "inactive"
        assert nodes["lyse"]["status"] == "active"

    def test_a_template_shows_no_hashes_of_its_own_beyond_its_version(
        self, pipelines, protocols
    ):
        guid = save(pipelines, protocols)["pipeline_guid"]
        found = get_pipeline(pipelines, protocols, guid)
        assert "node_hashes" not in found and "manifest_hash" not in found


class TestRetire:
    def test_a_retired_template_is_no_longer_listed_or_found(
        self, pipelines, protocols
    ):
        saved = save(pipelines, protocols)

        retired = retire(pipelines, saved["pipeline_guid"], LATER)

        assert retired["hash"] == saved["hash"]
        assert list_pipelines(pipelines, protocols) == []
        with pytest.raises(InactivePipelineError):
            get_pipeline(pipelines, protocols, saved["pipeline_guid"])

    def test_an_unknown_template_cannot_be_retired(self, pipelines):
        with pytest.raises(InactivePipelineError):
            retire(pipelines, "nope", LATER)


class TestPipelineVersions:
    def test_every_version_oldest_first(self, pipelines, protocols):
        first = save(pipelines, protocols)
        guid = first["pipeline_guid"]
        second = save(
            pipelines, protocols, body(dag={"lyse": [], "elute": ["lyse"]}), guid, LATER
        )

        versions = pipeline_versions(pipelines, guid)["versions"]

        assert [v["hash"] for v in versions] == [first["hash"], second["hash"]]
        assert versions[0]["deprecated_at"] == as_iso(LATER)
        assert versions[1]["deprecated_at"] is None

    def test_a_retired_template_keeps_its_history(self, pipelines, protocols):
        """Retired is not gone — the portal can still show what it was."""
        saved = save(pipelines, protocols)
        retire(pipelines, saved["pipeline_guid"], LATER)

        (version,) = pipeline_versions(pipelines, saved["pipeline_guid"])["versions"]

        assert version["deprecated_at"] == as_iso(LATER)

    def test_an_unknown_guid_is_refused(self, pipelines):
        with pytest.raises(UnknownPipelineError):
            pipeline_versions(pipelines, "nope")


# -----------------------------------------------------------------------------#
# 4. THE COMPOSE PORT — exporting a lock
# -----------------------------------------------------------------------------#
class TestExportLock:
    """Export is where versions are pinned: the saved template's guids resolve to
    the protocols active now, and the pinned pipeline is hashed into the lock."""

    @pytest.fixture
    def exported(self, pipelines, protocols):
        saved = save(pipelines, protocols)
        return saved, export_lock(pipelines, protocols, saved["pipeline_guid"], LATER)

    def test_every_protocol_the_template_runs_is_pinned(self, exported, protocols):
        _, lock = exported
        active = {p["protocol_uid"]: p["hash"] for p in list_protocols(protocols)}

        assert lock["entries"] == {
            uid: {"guid": guid, "hash": active[uid]}
            for uid, guid in (
                ("protocols_io:568614", LYSE),
                ("protocols_io:400843", ELUTE),
            )
        }
        assert set(lock["bodies"]) == {e["hash"] for e in lock["entries"].values()}

    def test_the_pinned_pipeline_carries_hashes_and_the_manifest(self, exported):
        saved, lock = exported
        pinned = lock["pipeline"]["pipelines"][saved["pipeline_guid"]]

        assert pinned["node_hashes"] == {
            "lyse": lock["entries"]["protocols_io:568614"]["hash"],
            "elute": lock["entries"]["protocols_io:400843"]["hash"],
        }
        assert pinned["manifest_hash"] == lock["manifest_hash"]

    def test_the_pinned_pipeline_is_its_own_version_of_the_template(self, exported):
        """Hashes filled in make a different content address — and the lock names
        the template it was pinned from."""
        saved, lock = exported
        pipeline = lock["pipeline"]

        assert pipeline["entries"][saved["pipeline_guid"]]["hash"] != saved["hash"]
        assert pipeline["provenance"] == {"template_hash": saved["hash"]}

    def test_exporting_writes_nothing_to_the_template_store(
        self, exported, pipelines, protocols
    ):
        saved, _ = exported
        (version,) = pipeline_versions(pipelines, saved["pipeline_guid"])["versions"]
        assert version["hash"] == saved["hash"]

    def test_a_protocol_run_twice_is_locked_once(self, pipelines, protocols):
        repeat = body(
            dag={"wash_1": ["wash_2"], "wash_2": []},
            nodes={"wash_1": LYSE, "wash_2": LYSE},
        )
        guid = save(pipelines, protocols, repeat)["pipeline_guid"]

        lock = export_lock(pipelines, protocols, guid, LATER)

        assert list(lock["entries"]) == ["protocols_io:568614"]

    def test_a_pins_only_lock_carries_no_bodies(self, pipelines, protocols):
        guid = save(pipelines, protocols)["pipeline_guid"]

        lock = export_lock(pipelines, protocols, guid, LATER, with_bodies=False)

        assert lock["entries"]
        assert "bodies" not in lock

    def test_the_same_template_later_pins_the_newer_version(
        self, pipelines, protocols, artefacts
    ):
        guid = save(pipelines, protocols)["pipeline_guid"]
        before = export_lock(pipelines, protocols, guid, LATER)
        revised = [
            a if a.id != 568614 else type(a)(**{**a.__dict__, "title": "revised"})
            for a in artefacts
        ]
        write_protocols(
            protocols, format_protocol_entry(revised, LATER), LATER, "protocols_io"
        )

        after = export_lock(pipelines, protocols, guid, LATEST)

        uid = "protocols_io:568614"
        assert before["entries"][uid]["hash"] != after["entries"][uid]["hash"]
        assert before["manifest_hash"] != after["manifest_hash"]

    def test_an_inactive_protocol_is_refused_by_node(self, pipelines, protocols):
        """A lock is a guarantee. The error names the step to swap out."""
        guid = save(pipelines, protocols)["pipeline_guid"]
        retire_protocol(protocols, "protocols_io:400843")

        with pytest.raises(UnresolvedProtocolError) as raised:
            export_lock(pipelines, protocols, guid, LATER)
        assert raised.value.nodes == {"elute": ELUTE}

    def test_a_retired_template_cannot_be_exported(self, pipelines, protocols):
        guid = save(pipelines, protocols)["pipeline_guid"]
        retire(pipelines, guid, LATER)

        with pytest.raises(InactivePipelineError):
            export_lock(pipelines, protocols, guid, LATEST)
