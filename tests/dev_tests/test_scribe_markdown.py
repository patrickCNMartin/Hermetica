# -----------------------------------------------------------------------------#
# TESTS — a lock document rendered as markdown
# -----------------------------------------------------------------------------#
"""A pin that is still the live version links to protocols.io; a pin that is not
has no canonical source left, so its body is inlined. Without a database that
distinction is unknowable and everything inlines, which keeps a standalone lock
renderable."""

import copy
import json

import pytest

from scribe.markdown import (
    OrderError,
    UnrenderableProtocolError,
    export_markdown,
    plain,
    resolve_order,
    to_markdown,
)
from seal.seal import generate_protocol_lock
from seal.store import (
    active_hashes,
    connect,
    format_db_entry,
    initialize_protocol_db,
    write_pull,
)
from sources.protocols_io.artefact import build_protocol_artefact
from utils.dates import to_epoch

PULLED_AT = to_epoch("2026-07-27")
LATER = to_epoch("2026-08-01")

LINK_MARKER = "Read the current protocol:"


@pytest.fixture
def store(db_path, by_id_records):
    initialize_protocol_db(db_path)
    artefacts = [
        build_protocol_artefact(copy.deepcopy(r)) for r in by_id_records.values()
    ]
    write_pull(db_path, format_db_entry(artefacts, PULLED_AT), PULLED_AT)
    with connect(db_path, read_only=True) as conn:
        return db_path, list(active_hashes(conn).values())


@pytest.fixture
def lock(store):
    db, hashes = store
    return generate_protocol_lock(hashes, db, as_of=PULLED_AT), db


def deprecate(db: str, protocol_id: str) -> None:
    """Close a protocol's interval so its pin stops being the live version."""
    with connect(db) as conn:
        conn.execute(
            "UPDATE protocol_history SET deprecated_at = ? WHERE protocol_id = ?",
            (LATER, protocol_id),
        )


def section(rendered: str, protocol_id: str) -> str:
    """One protocol's block. Names repeat across the fixture, so an assertion
    about one protocol must not be satisfiable by another's text."""
    blocks = rendered.split("\n## ")
    match = [b for b in blocks if f"| protocol_id | {protocol_id} |" in b]
    if len(match) != 1:
        raise AssertionError(f"{len(match)} blocks for protocol {protocol_id}")
    return match[0]


def with_steps(document: dict) -> str:
    """A pinned protocol_id whose body actually carries steps."""
    for pid, entry in sorted(document["entries"].items()):
        if document["bodies"][entry["hash"]].get("chain"):
            return pid
    raise AssertionError("the fixture must include a protocol with steps")


# -----------------------------------------------------------------------------#
# 1. ORDER
# -----------------------------------------------------------------------------#
class TestOrder:
    def test_default_is_sorted(self):
        assert resolve_order({"b": {}, "a": {}}, None) == ["a", "b"]

    def test_the_caller_order_is_honoured(self, lock):
        document, db = lock
        wanted = list(reversed(sorted(document["entries"])))
        rendered = to_markdown(document, db=db, order=wanted)
        positions = [line for line in rendered.splitlines() if line.startswith("## ")]
        assert len(positions) == len(wanted)

    def test_a_missing_id_raises(self):
        """A subset would silently drop a protocol from a document claiming
        to be the whole manifest."""
        with pytest.raises(OrderError, match="missing"):
            resolve_order({"a": {}, "b": {}}, ["a"])

    def test_an_unknown_id_raises(self):
        with pytest.raises(OrderError, match="unknown"):
            resolve_order({"a": {}}, ["a", "z"])

    def test_a_repeated_id_raises(self):
        with pytest.raises(OrderError, match="repeats"):
            resolve_order({"a": {}, "b": {}}, ["a", "a", "b"])


# -----------------------------------------------------------------------------#
# 2. MODE RESOLUTION
# -----------------------------------------------------------------------------#
class TestMode:
    def test_all_pins_live_gives_all_links(self, lock):
        document, db = lock
        rendered = to_markdown(document, db=db)
        assert rendered.count(LINK_MARKER) == len(document["entries"])
        assert "### Steps" not in rendered

    def test_no_db_inlines_everything(self, lock):
        document, _ = lock
        rendered = to_markdown(document)
        assert LINK_MARKER not in rendered
        assert "### Steps" in rendered

    def test_no_db_says_activeness_is_unknown(self, lock):
        """Without a store we cannot claim these pins are stale — only that we
        did not check."""
        document, _ = lock
        assert "unknown" in to_markdown(document)

    def test_a_deprecated_pin_inlines_while_the_rest_link(self, lock):
        document, db = lock
        deprecate(db, with_steps(document))
        rendered = to_markdown(document, db=db)
        assert rendered.count(LINK_MARKER) == len(document["entries"]) - 1
        assert rendered.count("pinned version inlined") == 1
        assert rendered.count("### Steps") == 1

    def test_the_banner_counts_match_the_body(self, lock):
        document, db = lock
        deprecate(db, sorted(document["entries"])[0])
        rendered = to_markdown(document, db=db)
        total = len(document["entries"])
        assert f"{total - 1} rendered as links" in rendered
        assert f"{1} inlined in full" in rendered

    def test_a_superseded_pin_inlines_though_its_protocol_is_still_active(
        self, lock, by_id_records
    ):
        """The protocol still has a live version — just not this one. Linking here
        would show today's content under yesterday's hash."""
        document, db = lock
        victim = with_steps(document)
        edited = []
        for raw in by_id_records.values():
            raw = copy.deepcopy(raw)
            if str(raw["id"]) == victim:
                raw["title"] = f"{raw['title']} (revised)"
            edited.append(build_protocol_artefact(raw))
        write_pull(db, format_db_entry(edited, LATER), LATER)

        with connect(db, read_only=True) as conn:
            assert active_hashes(conn)[victim] != document["entries"][victim]["hash"]
        rendered = to_markdown(document, db=db)
        assert rendered.count("pinned version inlined") == 1
        assert rendered.count("### Steps") == 1


# -----------------------------------------------------------------------------#
# 3. CONTENT
# -----------------------------------------------------------------------------#
class TestContent:
    def test_section_html_is_unescaped(self):
        assert plain("<p>Kantele &amp; QC</p>") == "Kantele & QC"

    def test_steps_follow_the_chain_not_the_list(self, lock):
        document, _ = lock
        body = next(
            b for b in document["bodies"].values() if len(b.get("chain") or []) > 2
        )
        # Reverse the stored list; the chain is what must drive the output.
        body["steps"] = list(reversed(body["steps"]))
        rendered = to_markdown(document)
        first = body["chain"][0]
        text = next(s for s in body["steps"] if s["id"] == first)["step"]
        assert isinstance(text, str)
        assert "**Step 1**" in rendered

    def test_a_chain_that_disagrees_with_steps_raises(self, lock):
        """chain is the contract for order — a mismatch is corruption, not a
        formatting problem."""
        document, _ = lock
        body = next(iter(document["bodies"].values()))
        body["chain"] = list(body["chain"]) + [999999]
        with pytest.raises(ValueError, match="chain does not cover steps"):
            to_markdown(document)

    @pytest.mark.parametrize(
        "field, heading",
        [
            ("warning", "### Warning"),
            ("disclaimer", "### Disclaimer"),
            ("guidelines", "### Guidelines"),
            ("before_start", "### Before you start"),
            ("protocol_references", "### References"),
        ],
    )
    def test_a_populated_section_reaches_the_page(self, lock, field, heading):
        document, _ = lock
        body = next(iter(document["bodies"].values()))
        body[field] = json.dumps({"blocks": [{"text": "Kantele"}], "entityMap": {}})
        assert heading in to_markdown(document)

    @pytest.mark.parametrize(
        "heading",
        ["### Warning", "### Disclaimer", "### Guidelines", "### Before you start"],
    )
    def test_an_empty_section_is_omitted(self, lock, heading):
        document, _ = lock
        for body in document["bodies"].values():
            for field in ("warning", "disclaimer", "guidelines", "before_start"):
                body[field] = ""
        assert heading not in to_markdown(document)

    def test_before_you_start_precedes_materials(self, lock):
        document, _ = lock
        body = next(iter(document["bodies"].values()))
        for field in ("before_start", "materials"):
            body[field] = json.dumps({"blocks": [{"text": "Kantele"}], "entityMap": {}})
        rendered = to_markdown(document)
        assert rendered.index("### Before you start") < rendered.index("### Materials")

    def test_a_reagent_entity_renders_its_name_not_a_marker(self, lock):
        document, _ = lock
        body = next(iter(document["bodies"].values()))
        body["materials"] = json.dumps(
            {
                "blocks": [
                    {
                        "text": " ",
                        "entityRanges": [{"key": 0, "offset": 0, "length": 1}],
                    }
                ],
                "entityMap": {
                    "0": {
                        "type": "reagents",
                        "data": {"name": "Trizma base", "sku": "T1503", "vendor": {}},
                    }
                },
            }
        )
        rendered = to_markdown(document)
        assert "Trizma base (T1503)" in rendered
        assert "[reagents]" not in rendered

    def test_the_manifest_hash_is_reported(self, lock):
        document, _ = lock
        assert document["manifest_hash"] in to_markdown(document)

    def test_a_tampered_pin_set_is_flagged_in_the_output(self, lock):
        document, _ = lock
        document["manifest_hash"] = "sha256:" + "0" * 64
        assert "DOES NOT MATCH" in to_markdown(document)


# -----------------------------------------------------------------------------#
# 4. ATTRIBUTION
# -----------------------------------------------------------------------------#
class TestAttribution:
    """Creator and authors are different claims — who owns the upstream record
    versus who is credited — so they get a row each. They overlap often; that is
    the source's own duplication, not something to dedupe away."""

    def test_creator_and_affiliation_are_their_own_rows(self, lock, by_id_records):
        document, _ = lock
        pid = str(by_id_records["signed_urls"]["id"])
        rendered = section(to_markdown(document), pid)
        assert "| creator | Otto Doe |" in rendered
        assert "| affiliation | Department of Placeholders |" in rendered

    def test_a_creator_renders_when_there_are_no_authors(self, lock, by_id_records):
        """Half the live set has an empty `authors` — without this row those
        protocols carry no attribution at all."""
        document, _ = lock
        pid = str(by_id_records["baseline"]["id"])
        assert document["protocols"][pid]["authors"] == []
        rendered = section(to_markdown(document), pid)
        assert "| creator | Pablo Personman |" in rendered
        assert "| authors |" not in rendered

    def test_creator_and_authors_both_render(self, lock, by_id_records):
        """The first author's name is an address — upstream really does that."""
        document, _ = lock
        pid = str(by_id_records["dotted_steps"]["id"])
        rendered = section(to_markdown(document), pid)
        assert "| creator | Jane Doe |" in rendered
        assert (
            "| authors | person.macpersonface@example.org, Pablo Personman, "
            "Jane Doe, Otto Example |"
        ) in rendered

    def test_a_lock_without_a_creator_omits_both_rows(self, lock, by_id_records):
        """A lock exported before creator was carried must still render."""
        document, _ = lock
        pid = str(by_id_records["signed_urls"]["id"])
        del document["protocols"][pid]["creator"]
        rendered = section(to_markdown(document), pid)
        assert "| creator |" not in rendered
        assert "| affiliation |" not in rendered

    def test_a_blank_affiliation_omits_only_that_row(self, lock, by_id_records):
        document, _ = lock
        pid = str(by_id_records["signed_urls"]["id"])
        document["protocols"][pid]["creator"]["affiliation"] = ""
        rendered = section(to_markdown(document), pid)
        assert "| creator | Otto Doe |" in rendered
        assert "| affiliation |" not in rendered

    def test_a_pins_only_lock_reads_the_creator_from_the_db(self, lock, by_id_records):
        """A pins-only lock carries neither display block nor body, so the store
        is the only source — creator is metadata and was never in the blob."""
        document, db = lock
        del document["protocols"]
        del document["bodies"]
        pid = str(by_id_records["signed_urls"]["id"])
        rendered = section(to_markdown(document, db=db), pid)
        assert "| creator | Otto Doe |" in rendered
        assert "| affiliation | Department of Placeholders |" in rendered


# -----------------------------------------------------------------------------#
# 5. WHAT IT REFUSES
# -----------------------------------------------------------------------------#
class TestRefuses:
    def test_a_body_needed_with_neither_bodies_nor_db_raises(self, lock):
        document, _ = lock
        del document["bodies"]
        with pytest.raises(UnrenderableProtocolError):
            to_markdown(document)

    def test_a_pins_only_lock_links_fine_with_a_db(self, lock):
        """No bodies are needed when every pin is live — that is the saving."""
        document, db = lock
        del document["bodies"]
        del document["protocols"]
        rendered = to_markdown(document, db=db)
        assert rendered.count(LINK_MARKER) == len(document["entries"])


# -----------------------------------------------------------------------------#
# 6. EXPORT
# -----------------------------------------------------------------------------#
class TestExport:
    def test_it_writes_what_it_returns(self, lock, tmp_path):
        document, db = lock
        path = str(tmp_path / "manifest.md")
        written = export_markdown(document, path, db=db)
        assert open(path, encoding="utf-8").read() == written
        assert written.startswith("# Protocol manifest")
