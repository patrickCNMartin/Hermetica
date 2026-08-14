# -----------------------------------------------------------------------------#
# TESTS — Draft.js rich text becomes readable protocol text
# -----------------------------------------------------------------------------#
"""The quantities live in `entityMap`, not in the block text: a block carries one
placeholder character where each entity belongs. A renderer that reads `text` alone
deletes every mass, volume, duration and temperature and still looks plausible."""

import json

from scribe.richtext import render_block, render_document, render_entity, unit_name

UNITS = {"1": "µL", "2": "mL", "6": "g", "10": "°C", "34": "x g"}


def block(text: str, *spans) -> dict:
    return {
        "text": text,
        "entityRanges": [
            {"key": key, "offset": offset, "length": length}
            for key, offset, length in spans
        ],
    }


def document(text: str, spans, entity_map: dict) -> str:
    return json.dumps({"blocks": [block(text, *spans)], "entityMap": entity_map})


# -----------------------------------------------------------------------------#
# 1. UNIT RESOLUTION
# -----------------------------------------------------------------------------#
class TestUnitName:
    def test_a_known_id_resolves(self):
        assert unit_name(6, UNITS) == "g"

    def test_an_unknown_id_is_marked_not_dropped(self):
        """3900 is cited by real protocols and absent from every upstream catalog."""
        assert unit_name(3900, UNITS) == "[unit:3900]"

    def test_no_unit_renders_nothing(self):
        assert unit_name(None, UNITS) == ""


# -----------------------------------------------------------------------------#
# 2. ENTITIES
# -----------------------------------------------------------------------------#
class TestEntities:
    def test_amount_carries_its_unit(self):
        entity = {"type": "amount", "data": {"amount": "1.211", "unit": 6}}
        assert render_entity(entity, UNITS) == "1.211 g"

    def test_concentration_with_an_unresolvable_unit_is_marked(self):
        entity = {"type": "concentration", "data": {"concentration": "1", "unit": 3900}}
        assert render_entity(entity, UNITS) == "1 [unit:3900]"

    def test_duration_is_seconds_and_needs_no_catalog(self):
        assert (
            render_entity({"type": "duration", "data": {"duration": 600}}, {})
            == "10 min"
        )

    def test_duration_splits_into_parts(self):
        assert (
            render_entity({"type": "duration", "data": {"duration": 3690}}, {})
            == "1 h 1 min 30 s"
        )

    def test_ph_is_labelled(self):
        assert render_entity({"type": "ph", "data": {"number": "8.0"}}, {}) == "pH 8.0"

    def test_centrifuge_joins_speed_temperature_and_time(self):
        entity = {
            "type": "centrifuge",
            "data": {
                "centrifuge": "13000",
                "unit": 34,
                "temperature": "23",
                "temperatureUnit": 10,
                "duration": 60,
            },
        }
        assert render_entity(entity, UNITS) == "13000 x g, 23 °C, 1 min"

    def test_link_becomes_a_url(self):
        entity = {"type": "link", "data": {"url": "https://example.org/x"}}
        assert render_entity(entity, {}) == "<https://example.org/x>"

    def test_a_reagent_names_its_vendor_and_sku(self):
        entity = {
            "type": "reagents",
            "data": {
                "name": "Trizma base",
                "sku": "T1503",
                "vendor": {"name": "MilliporeSigma"},
            },
        }
        assert render_entity(entity, {}) == "Trizma base (MilliporeSigma, T1503)"

    def test_a_reagent_with_no_vendor_or_sku_is_just_its_name(self):
        """Real entries carry an empty vendor and sku — "1X PBS (, )" is wrong."""
        entity = {
            "type": "reagents",
            "data": {"name": "1X PBS", "sku": "", "vendor": {"name": ""}},
        }
        assert render_entity(entity, {}) == "1X PBS"

    def test_a_reagent_with_a_null_vendor_still_renders(self):
        entity = {"type": "reagents", "data": {"name": "DTT", "sku": "D0632"}}
        assert render_entity(entity, {}) == "DTT (D0632)"

    def test_equipment_names_the_brand_not_the_reseller(self):
        """`vendor` is who sold it; `brand` is who made it."""
        entity = {
            "type": "equipment",
            "data": {
                "name": "Biomek i7",
                "brand": "Beckman Coulter",
                "sku": "B87585",
                "vendor": {"name": "Ramcon"},
            },
        }
        assert render_entity(entity, {}) == "Biomek i7 (Beckman Coulter, B87585)"

    def test_a_nameless_catalog_entry_is_marked_never_dropped(self):
        entity = {"type": "equipment", "data": {"brand": "Eppendorf", "sku": "X"}}
        assert render_entity(entity, {}) == "[equipment]"

    def test_an_unknown_type_is_marked_never_dropped(self):
        """Silence here would mean content vanishing with no trace in the output."""
        assert render_entity({"type": "invented", "data": {}}, {}) == "[invented]"

    def test_notes_and_tables_are_markers_for_now(self):
        assert render_entity({"type": "notes", "data": {}}, {}) == "[notes]"
        assert render_entity({"type": "tables", "data": {}}, {}) == "[tables]"


# -----------------------------------------------------------------------------#
# 3. SPLICING
# -----------------------------------------------------------------------------#
class TestSplicing:
    def test_the_quantity_survives(self):
        """The regression this module exists to prevent."""
        entity_map = {"0": {"type": "amount", "data": {"amount": "1.211", "unit": 6}}}
        text = render_block(
            block("Weigh approximately   of Trizma.", (0, 20, 1)), entity_map, UNITS
        )
        assert "1.211 g" in text
        assert text == "Weigh approximately 1.211 g of Trizma."

    def test_two_entities_in_one_block_both_land(self):
        """Ascending splices would shift every later offset — order matters."""
        entity_map = {
            "0": {"type": "amount", "data": {"amount": "10", "unit": 2}},
            "1": {
                "type": "concentration",
                "data": {"concentration": "1", "unit": 3900},
            },
        }
        text = render_block(
            block("Prepare  of   aqueous solution.", (0, 8, 1), (1, 12, 1)),
            entity_map,
            UNITS,
        )
        assert text == "Prepare 10 mL of 1 [unit:3900] aqueous solution."

    def test_spacing_is_added_where_upstream_relied_on_the_editor_chip(self):
        """The placeholder often sits flush against a word, so a naive splice
        yields "10 mLof". Both sides get separated."""
        entity_map = {"0": {"type": "amount", "data": {"amount": "10", "unit": 2}}}
        assert render_block(block("Add of buffer", (0, 3, 1)), entity_map, UNITS) == (
            "Add 10 mL of buffer"
        )

    def test_no_space_is_forced_before_punctuation(self):
        entity_map = {"0": {"type": "amount", "data": {"amount": "10", "unit": 2}}}
        assert (
            render_block(block("Add  .", (0, 4, 1)), entity_map, UNITS) == "Add 10 mL."
        )

    def test_an_entity_missing_from_the_map_is_marked(self):
        assert "[entity:7]" in render_block(block("Add  now", (7, 4, 1)), {}, UNITS)

    def test_a_block_with_no_entities_is_untouched(self):
        assert render_block(block("Mix thoroughly."), {}, UNITS) == "Mix thoroughly."


# -----------------------------------------------------------------------------#
# 4. DOCUMENTS
# -----------------------------------------------------------------------------#
class TestDocument:
    def test_blocks_become_paragraphs(self):
        raw = json.dumps(
            {"blocks": [block("First."), block("Second.")], "entityMap": {}}
        )
        assert render_document(raw) == "First.\n\nSecond."

    def test_empty_blocks_are_dropped(self):
        raw = json.dumps(
            {"blocks": [block("First."), block("  "), block("Last.")], "entityMap": {}}
        )
        assert render_document(raw) == "First.\n\nLast."

    def test_a_double_encoded_string_is_parsed(self):
        raw = document(
            "Use  today",
            [(0, 4, 1)],
            {"0": {"type": "amount", "data": {"amount": "5", "unit": 1}}},
        )
        assert render_document(raw, UNITS) == "Use 5 µL today"

    def test_an_already_parsed_document_works_too(self):
        raw = {"blocks": [block("Plain.")], "entityMap": {}}
        assert render_document(raw) == "Plain."

    def test_non_rich_text_renders_empty(self):
        assert render_document("") == ""
        assert render_document(None) == ""
        assert render_document("just prose, not a document") == ""
