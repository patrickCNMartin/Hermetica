# -----------------------------------------------------------------------------#
# TESTS — the by-ID response becomes a ProtocolArtefact
# -----------------------------------------------------------------------------#
"""The artefact is the single intermediate between the API and the store, so a
field sourced from the wrong place silently mis-versions every protocol."""

import dataclasses
import json

import pytest

from seal.contract import (
    HASH_FIELDS,
    METADATA_FIELDS,
    build_protocol_artefact,
    get_step_chain,
    get_steps,
    get_unit_map,
    protocol_hash,
)
from tests.conftest import ARCHETYPES

# Restated as a literal on purpose: this is the spec. HASH_FIELDS defines protocol
# identity, so an unreviewed edit re-hashes every version in the store — changing
# the contract must require changing this test too.
SPEC_HASH_FIELDS = (
    "doi",
    "reserved_doi",
    "id",
    "guid",
    "title",
    "description",
    "disclaimer",
    "warning",
    "materials",
    "steps",
    "chain",
    "units",
    "uri",
    "version_class",
    "protocol_references",
)
SPEC_METADATA_FIELDS = ("created_on", "creator", "authors", "keywords")

STEP_CONTENT_FIELDS = {"id", "guid", "section", "step", "critical"}


# -----------------------------------------------------------------------------#
# 1. THE CONTRACT ITSELF
# -----------------------------------------------------------------------------#
class TestContract:
    def test_hash_fields_match_the_spec(self):
        assert HASH_FIELDS == SPEC_HASH_FIELDS

    def test_metadata_fields_match_the_spec(self):
        assert METADATA_FIELDS == SPEC_METADATA_FIELDS

    def test_the_two_sets_do_not_overlap(self):
        assert not set(HASH_FIELDS) & set(METADATA_FIELDS)

    @pytest.mark.parametrize("archetype", ARCHETYPES)
    def test_hashable_is_exactly_hash_fields(self, record, archetype):
        built = build_protocol_artefact(record(archetype))
        assert tuple(built.hashable()) == HASH_FIELDS

    @pytest.mark.parametrize("archetype", ARCHETYPES)
    def test_metadata_is_exactly_metadata_fields(self, record, archetype):
        built = build_protocol_artefact(record(archetype))
        assert tuple(built.metadata()) == METADATA_FIELDS

    def test_to_dict_is_the_union(self, record):
        built = build_protocol_artefact(record("baseline"))
        assert set(built.to_dict()) == set(HASH_FIELDS) | set(METADATA_FIELDS)

    def test_artefact_is_frozen(self, record):
        """A snapshot, not a working buffer — mutating desyncs blob and hash."""
        built = build_protocol_artefact(record("baseline"))
        with pytest.raises(dataclasses.FrozenInstanceError):
            built.title = "changed"


# -----------------------------------------------------------------------------#
# 2. WHERE EACH FIELD COMES FROM
# -----------------------------------------------------------------------------#
class TestFieldSourcing:
    def test_top_level_fields_are_read_from_the_top_level(self, record):
        raw = record("baseline")
        built = build_protocol_artefact(raw)
        assert built.id == raw["id"]
        assert built.guid == raw["guid"]
        assert built.title == raw["title"]
        assert built.uri == raw["uri"]
        assert built.reserved_doi == raw["reserved_doi"]
        assert built.version_class == raw["version_class"]

    def test_materials_comes_from_materials_text(self, record):
        raw = record("signed_urls")
        assert build_protocol_artefact(raw).materials != raw["materials"]

    def test_doi_comes_from_the_top_level(self, record):
        raw = record("baseline")
        raw["doi"] = "10.99999/example.org.toplevel"
        assert build_protocol_artefact(raw).doi == raw["doi"]

    def test_version_class_can_differ_from_id(self, record):
        """Upstream lets these diverge — reading one for the other is a bug."""
        built = build_protocol_artefact(record("version_class_differs"))
        assert built.version_class != built.id

    @pytest.mark.parametrize("archetype", ARCHETYPES)
    def test_nothing_is_sourced_from_versions(self, record, archetype):
        """`versions` is the version *family* keyed on the root, so for a non-root
        record it describes the ancestor. Editing it must not move the hash."""
        raw = record(archetype)
        before = protocol_hash(build_protocol_artefact(raw))
        raw["versions"] = [{"id": 1, "version_code": "zzzz", "modified_on": 9}]
        assert protocol_hash(build_protocol_artefact(raw)) == before


# -----------------------------------------------------------------------------#
# 3. FALLBACKS — a null top-level `doi`
# -----------------------------------------------------------------------------#
class TestDoiFallback:
    @pytest.mark.parametrize("archetype", ARCHETYPES)
    def test_doi_falls_back_to_empty(self, record, archetype):
        """Upstream sends doi=null for anything unpublished."""
        assert build_protocol_artefact(record(archetype)).doi == ""

    def test_doi_never_falls_back_to_reserved_doi(self, record):
        """Two fields holding one string would make an unissued DOI look issued."""
        built = build_protocol_artefact(record("reserved_doi"))
        assert built.reserved_doi
        assert built.doi != built.reserved_doi


# -----------------------------------------------------------------------------#
# 4. STEPS AND CHAIN
# -----------------------------------------------------------------------------#
class TestSteps:
    def test_null_steps_become_empty(self, record):
        """Upstream sends steps=null, so a .get default never fires."""
        built = build_protocol_artefact(record("empty_versions_null_steps"))
        assert built.steps == []
        assert built.chain == []

    def test_steps_are_trimmed_to_content_only(self, record):
        for step in build_protocol_artefact(record("dotted_steps")).steps:
            assert set(step) <= STEP_CONTENT_FIELDS

    def test_ordering_is_not_kept_on_the_step(self, record):
        """`number` lives in the chain, not the step — that is the whole split."""
        built = build_protocol_artefact(record("dotted_steps"))
        assert all("number" not in s for s in built.steps)

    def test_chain_covers_every_step(self, record):
        built = build_protocol_artefact(record("dotted_steps"))
        assert sorted(built.chain) == sorted(s["id"] for s in built.steps)


class TestChainOrdering:
    """`number` is a dotted string; sorting it as text puts step 10 before step 2."""

    def test_chain_is_execution_order(self, record):
        raw = record("dotted_steps")
        order = {s["id"]: s["number"] for s in raw["steps"]}
        numbers = [order[i] for i in build_protocol_artefact(raw).chain]
        assert numbers == sorted(
            numbers, key=lambda n: tuple(int(p) for p in n.split("."))
        )

    def test_ten_sorts_after_two(self, record):
        raw = record("dotted_steps")
        order = {s["id"]: s["number"] for s in raw["steps"]}
        numbers = [order[i] for i in build_protocol_artefact(raw).chain]
        assert numbers.index("2") < numbers.index("10")

    def test_substeps_sit_inside_their_parent(self):
        steps = [
            {"id": n, "number": v}
            for n, v in enumerate(["8", "7", "7.10", "7.2", "10", "2"])
        ]
        numbers = {s["id"]: s["number"] for s in steps}
        assert [numbers[i] for i in get_step_chain(steps)] == [
            "2",
            "7",
            "7.2",
            "7.10",
            "8",
            "10",
        ]

    def test_a_malformed_number_raises(self):
        """Fail loudly rather than fall back to an order that is silently wrong."""
        with pytest.raises(ValueError):
            get_step_chain([{"id": 1, "number": "one"}])


class TestReorderVsRewrite:
    """Splitting steps from chain is what makes these two distinguishable."""

    def test_a_reorder_changes_the_chain_but_not_the_steps(self, record):
        raw = record("dotted_steps")
        before = build_protocol_artefact(raw)
        reversed_numbers = [s["number"] for s in raw["steps"]][::-1]
        for step, number in zip(raw["steps"], reversed_numbers):
            step["number"] = number
        after = build_protocol_artefact(raw)

        assert after.chain != before.chain
        assert after.steps == before.steps
        assert protocol_hash(after) != protocol_hash(before)

    def test_a_rewrite_changes_the_steps_but_not_the_chain(self, record):
        raw = record("dotted_steps")
        before = build_protocol_artefact(raw)
        raw["steps"][0]["step"] = '{"blocks":[{"key":"x","text":"Rewritten."}]}'
        after = build_protocol_artefact(raw)

        assert after.chain == before.chain
        assert after.steps != before.steps
        assert protocol_hash(after) != protocol_hash(before)


# -----------------------------------------------------------------------------#
# 4b. THE UNIT MAP
# -----------------------------------------------------------------------------#
class TestUnitMap:
    """Upstream `units` is a shared catalog, not protocol content: ~45 unused
    entries per protocol and it churns on its own. Only the cited subset is
    hashed, so a catalog edit cannot re-fork a protocol nothing changed in."""

    def test_only_cited_units_are_kept(self, record):
        raw = record("dotted_steps")
        catalog = {unit["id"] for unit in raw["units"]}
        kept = {int(uid) for uid in get_unit_map(raw)}
        assert kept < catalog, "the map must be a strict subset of the catalog"
        assert kept == {1, 2, 5, 6, 13, 28}

    def test_names_resolve_against_the_catalog(self, record):
        assert get_unit_map(record("dotted_steps"))["6"] == "g"

    def test_growing_the_catalog_does_not_change_the_map(self, record):
        """The whole point: an upstream catalog edit must not re-fork a protocol."""
        raw = record("dotted_steps")
        before = protocol_hash(build_protocol_artefact(raw))
        raw["units"].append(
            {"id": 999, "name": "parsec", "aliases": [], "deleted": False}
        )
        assert protocol_hash(build_protocol_artefact(raw)) == before

    def test_an_uncited_catalog_entry_is_excluded(self, record):
        raw = record("dotted_steps")
        uncited = [u["id"] for u in raw["units"] if u["id"] not in {1, 2, 5, 6, 13, 28}]
        assert uncited, "the fixture must carry spare entries to prove they are ignored"
        assert not {str(uid) for uid in uncited} & set(get_unit_map(raw))

    def test_an_unresolvable_id_is_omitted_not_guessed(self, record):
        """3900 is cited by this record and absent from every upstream catalog."""
        raw = record("dotted_steps")
        assert "3900" not in get_unit_map(raw)

    def test_units_are_collected_from_nested_documents(self):
        """A `notes` entity carries its own blocks and entityMap, arbitrarily deep."""
        nested = {
            "blocks": [
                {"text": " ", "entityRanges": [{"key": 0, "offset": 0, "length": 1}]}
            ],
            "entityMap": {"0": {"type": "amount", "data": {"amount": "5", "unit": 2}}},
        }
        outer = {
            "blocks": [
                {"text": " ", "entityRanges": [{"key": 0, "offset": 0, "length": 1}]}
            ],
            "entityMap": {"0": {"type": "notes", "data": nested}},
        }
        protocol = {
            "description": json.dumps(outer),
            "units": [{"id": 2, "name": "mL"}],
            "steps": [],
        }
        assert get_unit_map(protocol) == {"2": "mL"}

    def test_a_record_citing_nothing_maps_nothing(self, record):
        assert get_unit_map(record("baseline")) == {}

    def test_units_ride_in_the_hash(self, record):
        raw = record("dotted_steps")
        before = protocol_hash(build_protocol_artefact(raw))
        for unit in raw["units"]:
            if unit["id"] == 6:
                unit["name"] = "grammes"
        assert protocol_hash(build_protocol_artefact(raw)) != before


# -----------------------------------------------------------------------------#
# 5. WHAT IS IGNORED, AND WHAT IS REQUIRED
# -----------------------------------------------------------------------------#
class TestAllowlistBehaviour:
    def test_unknown_upstream_field_is_ignored(self, record):
        """An allowlist admits nothing we did not ask for."""
        raw = record("baseline")
        before = protocol_hash(build_protocol_artefact(raw))
        raw["some_new_upstream_field"] = {"invented": True}
        assert protocol_hash(build_protocol_artefact(raw)) == before

    def test_traffic_stats_do_not_version(self, record):
        raw = record("baseline")
        before = protocol_hash(build_protocol_artefact(raw))
        raw["stats"] = {"number_of_views": 999_999}
        assert protocol_hash(build_protocol_artefact(raw)) == before

    def test_metadata_change_does_not_version(self, record):
        """Re-attribution is not a new protocol version."""
        raw = record("baseline")
        before = protocol_hash(build_protocol_artefact(raw))
        raw["creator"] = {"name": "Someone Else", "username": "someone.else"}
        raw["authors"] = [{"name": "Someone Else"}]
        assert protocol_hash(build_protocol_artefact(raw)) == before

    def test_title_change_does_version(self, record):
        """title is display-only for resolution, but it IS hashed content."""
        raw = record("baseline")
        before = protocol_hash(build_protocol_artefact(raw))
        raw["title"] = "A different title"
        assert protocol_hash(build_protocol_artefact(raw)) != before

    @pytest.mark.parametrize(
        "field", ("id", "guid", "title", "uri", "created_on", "materials_text")
    )
    def test_missing_required_field_raises(self, record, field):
        """Loud and early — a missing field must never hash as 'removed'.

        Still a bare KeyError today; it names neither the field nor the protocol.
        Tighten this when MissingStableFieldsError is replaced.
        """
        raw = record("baseline")
        del raw[field]
        with pytest.raises(KeyError):
            build_protocol_artefact(raw)


# -----------------------------------------------------------------------------#
# 6. DETERMINISM ACROSS THE WHOLE SET
# -----------------------------------------------------------------------------#
class TestDeterminism:
    @pytest.mark.parametrize("archetype", ARCHETYPES)
    def test_hash_is_stable_across_rebuilds(self, record, archetype):
        assert protocol_hash(build_protocol_artefact(record(archetype))) == (
            protocol_hash(build_protocol_artefact(record(archetype)))
        )

    def test_every_archetype_hashes_distinctly(self, by_id_records):
        hashes = {
            protocol_hash(build_protocol_artefact(r)) for r in by_id_records.values()
        }
        assert len(hashes) == len(by_id_records)

    def test_get_steps_tolerates_null(self):
        assert get_steps({"steps": None}) == []
        assert get_steps({}) == []
