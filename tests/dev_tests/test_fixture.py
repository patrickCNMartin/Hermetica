# -----------------------------------------------------------------------------#
# TESTS — the fixture itself
# -----------------------------------------------------------------------------#
"""Guards on the committed dataset, not on the code that reads it.

The fixture is a structural transcription of a real protocols.io pull: every
shape kept, every value synthetic. Both halves need defending — a regeneration
that reintroduced real data would be a disclosure, and one that flattened the
awkward shapes would quietly hollow out the tests that depend on them.
"""

import json
import re

import pytest

from tests.conftest import ARCHETYPES, FIXTURE

RAW = FIXTURE.read_text(encoding="utf-8")

# Real values from the source corpus. None of these may ever appear again.
FORBIDDEN = (
    "Karolinska", "SciLifeLab", "scilifelab", "clinicalgenomics",
    "protocols.io", "amazonaws", "Covaris", "covaris", "cryoPREP",
    "tissueTUBE", "AKIAWFTFYUBUZ2U2JGOS",
)


# -----------------------------------------------------------------------------#
# 1. NOTHING REAL SURVIVES
# -----------------------------------------------------------------------------#
class TestNoRealData:
    @pytest.mark.parametrize("term", FORBIDDEN)
    def test_identifying_term_is_absent(self, term):
        assert term not in RAW

    def test_no_email_addresses(self):
        """The source carries real staff addresses inside step rich text."""
        assert re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", RAW) == []

    def test_no_real_doi_prefix(self):
        """Real DOIs resolve to real records — synthetic ones must not."""
        assert "10.17504" not in RAW

    def test_every_url_points_at_example_org(self):
        hosts = set(re.findall(r"https?://([^/\s\"'<>\\]+)", RAW))
        assert hosts, "the fixture should still contain URLs"
        assert all(h.endswith("example.org") for h in hosts), hosts


# -----------------------------------------------------------------------------#
# 2. THE AWKWARD SHAPES ARE STILL THERE
# -----------------------------------------------------------------------------#
class TestStructureSurvives:
    def test_every_archetype_is_present(self, by_id_records):
        assert set(by_id_records) == set(ARCHETYPES)

    def test_null_steps_and_empty_versions(self, by_id_records):
        """`steps` is null, not absent — a .get default never fires on it."""
        record = by_id_records["empty_versions_null_steps"]
        assert record["steps"] is None
        assert record["versions"] == []

    def test_a_record_with_empty_versions_but_real_steps(self, by_id_records):
        record = by_id_records["empty_versions_with_steps"]
        assert record["versions"] == []
        assert len(record["steps"]) > 1

    def test_reserved_doi_is_populated_somewhere(self, by_id_records):
        assert by_id_records["reserved_doi"]["reserved_doi"]

    def test_version_class_can_differ_from_id(self, by_id_records):
        record = by_id_records["version_class_differs"]
        assert record["version_class"] != record["id"]

    def test_dotted_step_numbering_reaches_double_digits(self, by_id_records):
        """Without a step 10 the lexicographic-sort bug cannot be caught."""
        numbers = [s["number"] for s in by_id_records["dotted_steps"]["steps"]]
        assert "10" in numbers
        assert any("." in n for n in numbers)

    def test_step_numbers_are_strings(self, by_id_records):
        """Upstream sends them as text, which is what made sorting subtle."""
        for record in by_id_records.values():
            for step in record.get("steps") or []:
                assert isinstance(step["number"], str)


# -----------------------------------------------------------------------------#
# 3. THE SIGNED-URL MATERIAL THE SCRUB TESTS NEED
# -----------------------------------------------------------------------------#
class TestSignedUrlMaterial:
    @pytest.fixture
    def signed(self, by_id_records):
        return by_id_records["signed_urls"]

    def test_rich_text_carries_the_escaped_separator(self, signed):
        """Inside a double-encoded document upstream escapes & to \\u0026."""
        assert "\\u0026" in signed["materials_text"]

    def test_a_plain_url_field_carries_a_bare_separator(self, signed):
        """Both forms must exist or the regex is only half tested."""
        assert any("&X-Amz" in (d.get("url") or "") for d in signed["documents"])

    def test_a_full_signing_param_set_is_present(self):
        found = set(re.findall(r"(X-Amz-[A-Za-z]+|Key-Pair-Id|Policy)=", RAW))
        assert {"X-Amz-Signature", "X-Amz-Credential", "X-Amz-Date"} <= found

    def test_signing_values_are_non_empty(self):
        """A pre-blanked fixture would make the scrub look like a no-op."""
        values = re.findall(r"X-Amz-Signature=([0-9a-zA-Z]*)", RAW)
        assert values and all(values)


# -----------------------------------------------------------------------------#
# 4. THE FILE ITSELF
# -----------------------------------------------------------------------------#
class TestFixtureFile:
    def test_it_is_sorted_and_newline_terminated(self):
        """Written deterministically, so regeneration produces a clean diff."""
        document = json.loads(RAW)
        assert list(document) == sorted(document)
        assert RAW.endswith("\n")

    def test_ids_are_unique_across_archetypes(self, by_id_records):
        ids = [r["id"] for r in by_id_records.values()]
        assert len(set(ids)) == len(ids)
