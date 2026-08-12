# -----------------------------------------------------------------------------#
# TESTS — the fixture itself
# -----------------------------------------------------------------------------#
"""Guards on the committed dataset, not on the code that reads it.

There is no generator: `protocols_by_id.json` is the source of truth, hand-held
dummy data shaped like a protocols.io by-ID pull. Two halves need defending —
a hand-edit that pasted real data in would be a disclosure, and one that
flattened the awkward shapes would quietly hollow out the tests that depend on
them.

The synthetic-ness guards are **allowlists**, for the same reason HASH_FIELDS is
one. A denylist of real terms can only work by naming the real terms, so the
guard file ends up holding the very values it exists to keep out — which is
exactly what happened before. An allowlist names nothing real.
"""

import json
import re
import unicodedata

import pytest

from tests.conftest import ARCHETYPES, FIXTURE, WALK_FIXTURE

RAW = unicodedata.normalize("NFC", FIXTURE.read_text(encoding="utf-8"))
WALK_RAW = unicodedata.normalize("NFC", WALK_FIXTURE.read_text(encoding="utf-8"))

# Every committed dataset. A fixture that is not scanned is a fixture that can
# leak, so a new one joins this tuple rather than getting its own weaker rules.
DATASETS = (("protocols_by_id", RAW), ("filemanager_walk", WALK_RAW))

# RFC 2606 reserves example.org permanently, so no synthetic value here can ever
# resolve to a real host or mailbox. 10.99999 is not an assigned DOI prefix.
RESERVED_DOMAIN = "example.org"
DOI_PREFIX = "10.99999"

# Every word of human-readable text in the fixture. Slugs, guids and hex are
# excluded — they are structurally random and carry no meaning; prose is where
# an identifying term would actually hide.
LEXICON = frozenset(
    """
    ada aliquot bench buffer café cartridge centrifuge column contact council
    department digest doe eluate elution example examples filtrate gradient
    incubate ines institute jane lysate macpersonface mira mixer of org otto
    overnight pablo pellet peptide person personman placeholders plate protocol
    reagent research resuspend rodney sam sample sampleson supernatant testcase
    trash vortex wash
    """.split()
)

# The fixture is scanned by detect-secrets and its findings are baselined: the
# signing material is meaningless by construction. These shapes are the tripwire
# for a *real* credential arriving by hand-edit. The AWS row is why the fixture's
# own key id is prefixed EXAMPLEKEYID rather than AKIA.
CREDENTIAL_SHAPES = (
    ("aws access key id", r"(?:AKIA|ASIA|AIDA|AROA|AGPA|ANPA)[0-9A-Z]{16}"),
    ("pem private key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("jwt", r"eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+"),
    ("github token", r"gh[pousr]_[A-Za-z0-9]{20,}"),
    ("slack token", r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    ("google api key", r"AIza[0-9A-Za-z_-]{35}"),
    ("basic auth in url", r"://[^/\s:@\"]+:[^/\s:@\"]+@"),
    ("named credential field", r"\"(?:password|secret|api_?key|token)\":\s*\"[^\"]+\""),
)

# Fields holding text a human reads. `units` is skipped: it is a shared SI
# catalog ("Celsius", "microliter") that identifies nobody.
PROSE_KEYS = frozenset(
    {
        "title",
        "title_html",
        "section",
        "keywords",
        "affiliation",
        "first_name",
        "last_name",
        "name",
        "funder_name",
        "info",
    }
)
RICH_KEYS = frozenset(
    {
        "description",
        "warning",
        "disclaimer",
        "guidelines",
        "materials_text",
        "before_start",
        "protocol_references",
        "step",
        "acknowledgements",
        "ethics_statement",
        "manuscript_citation",
    }
)


def _block_text(document, found: list[str]) -> None:
    """Collect Draft.js block text, recursing into nested documents."""
    if isinstance(document, dict):
        for block in document.get("blocks") or []:
            if block.get("text"):
                found.append(block["text"])
        for key, value in document.items():
            if key != "blocks":
                _block_text(value, found)
    elif isinstance(document, list):
        for value in document:
            _block_text(value, found)
    elif isinstance(document, str) and document.lstrip().startswith('{"blocks"'):
        try:
            _block_text(json.loads(document), found)
        except json.JSONDecodeError:
            pass


def prose(records: dict) -> list[str]:
    """Every human-readable string in the dataset, rich text flattened."""
    found: list[str] = []

    def walk(value, key=None, in_units=False):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k, in_units or key == "units")
        elif isinstance(value, list):
            for v in value:
                walk(v, key, in_units)
        elif isinstance(value, str) and value and not in_units:
            if key in RICH_KEYS or value.lstrip().startswith('{"blocks"'):
                _block_text(value, found)
            elif key in PROSE_KEYS:
                found.append(value)

    for record in records.values():
        walk(record)
    return found


# -----------------------------------------------------------------------------#
# 1. ONLY SYNTHETIC VALUES — allowlists, so nothing real is ever named here
# -----------------------------------------------------------------------------#
class TestOnlySyntheticValues:
    def test_prose_uses_only_the_synthetic_lexicon(self, by_id_records):
        """The strongest guard: a pasted real protocol fails on its first word."""
        words = {
            word.lower()
            for text in prose(by_id_records)
            for word in re.findall(
                r"[A-Za-zÀ-ÿ]{2,}", unicodedata.normalize("NFC", text)
            )
        }
        assert words <= LEXICON, sorted(words - LEXICON)

    def test_prose_in_the_walk_fixture_uses_the_same_lexicon(self, walk_records):
        """Folder names are prose too — a real workspace tree fails here."""
        words = {
            word.lower()
            for text in prose(walk_records)
            for word in re.findall(
                r"[A-Za-zÀ-ÿ]{2,}", unicodedata.normalize("NFC", text)
            )
        }
        assert words <= LEXICON, sorted(words - LEXICON)

    @pytest.mark.parametrize("dataset, raw", DATASETS)
    def test_every_url_host_is_reserved(self, dataset, raw):
        hosts = set(re.findall(r"https?://([^/\s\"'<>\\]+)", raw))
        assert all(h.endswith(RESERVED_DOMAIN) for h in hosts), sorted(hosts)

    def test_the_by_id_fixture_still_contains_urls(self):
        """The scrub tests need them; a fixture with none passes vacuously."""
        assert re.findall(r"https?://([^/\s\"'<>\\]+)", RAW)

    @pytest.mark.parametrize("dataset, raw", DATASETS)
    def test_every_email_uses_the_reserved_domain(self, dataset, raw):
        found = {m.rstrip(".") for m in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", raw)}
        assert all(m.endswith("@" + RESERVED_DOMAIN) for m in found), sorted(found)

    def test_emails_are_present_at_all(self):
        """Upstream carries them in author name fields and step rich text.

        A fixture with none passes the domain guard vacuously and leaves that
        shape untested — which is how the real addresses went missing before.
        """
        assert re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", RAW)

    @pytest.mark.parametrize("dataset, raw", DATASETS)
    def test_every_doi_uses_the_unassigned_prefix(self, dataset, raw):
        found = set(re.findall(r"\b10\.\d{4,9}/[^\"\s\\]+", raw))
        assert all(d.startswith(DOI_PREFIX + "/") for d in found), sorted(found)

    @pytest.mark.parametrize("dataset, raw", DATASETS)
    @pytest.mark.parametrize("name, pattern", CREDENTIAL_SHAPES)
    def test_no_credential_shaped_value(self, name, pattern, dataset, raw):
        """Tripwire for a real credential arriving by hand-edit."""
        assert re.findall(pattern, raw) == [], f"{dataset}: {name}"


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

    def test_first_step_guid_is_a_guid_that_resolves(self, by_id_records):
        """It points into `steps`; prose there would be a shape nobody has."""
        for record in by_id_records.values():
            guids = {s["guid"] for s in record.get("steps") or []}
            for entry in record.get("table_of_contents") or []:
                assert re.fullmatch(r"[0-9A-F]{32}", entry["first_step_guid"])
                if guids:
                    assert entry["first_step_guid"] in guids

    def test_critical_icons_are_enum_tokens(self, by_id_records):
        """Upstream sends CriticalIcon/OptionalIcon, never free text."""
        for record in by_id_records.values():
            for step in record.get("steps") or []:
                for critical in step.get("critical") or []:
                    assert critical["icon"] in {"CriticalIcon", "OptionalIcon"}

    def test_a_bare_text_section_exists(self, by_id_records):
        """`section` is hashed and rendered, and upstream is not always HTML."""
        sections = [
            step["section"]
            for record in by_id_records.values()
            for step in record.get("steps") or []
            if step.get("section")
        ]
        assert any("<p>" in s for s in sections)
        assert any("<" not in s for s in sections)


# -----------------------------------------------------------------------------#
# 3. THE SIGNED-URL MATERIAL THE SCRUB TESTS NEED
# -----------------------------------------------------------------------------#
class TestSignedUrlMaterial:
    def test_rich_text_carries_the_escaped_separator(self, by_id_records):
        """Inside a double-encoded document upstream escapes & to \\u0026."""
        assert "\\u0026" in by_id_records["signed_urls"]["materials_text"]

    def test_a_plain_url_field_carries_a_bare_separator(self, by_id_records):
        """Both forms must exist or the regex is only half tested."""
        documents = by_id_records["signed_urls"]["documents"]
        assert any("&X-Amz" in (d.get("url") or "") for d in documents)

    def test_a_full_signing_param_set_is_present(self):
        found = set(re.findall(r"(X-Amz-[A-Za-z]+|Key-Pair-Id|Policy)=", RAW))
        assert {"X-Amz-Signature", "X-Amz-Credential", "X-Amz-Date"} <= found

    def test_signing_values_are_non_empty(self):
        """A pre-blanked fixture would make the scrub look like a no-op."""
        values = re.findall(r"X-Amz-Signature=([0-9a-zA-Z]*)", RAW)
        assert values and all(values)

    def test_credential_carries_a_raw_path_separator(self):
        """A real X-Amz-Credential is `<key>/<date>/<region>/s3/aws4_request`.

        The scrub's value class permits `/`; with a slash-free value that was
        never actually exercised, so tightening it would go unnoticed.
        """
        values = re.findall(r"X-Amz-Credential=([^&\"'\s\\]*)", RAW)
        assert values and all("/" in v for v in values)

    def test_policy_carries_base64_padding(self):
        """CloudFront policies use the `~_-` alphabet and `=` padding."""
        values = re.findall(r"[?&]Policy=([^&\"'\s\\]*)", RAW)
        assert values and all(v.endswith("=") and "~" in v for v in values)


# -----------------------------------------------------------------------------#
# 4. THE FILE ITSELF
# -----------------------------------------------------------------------------#
class TestFixtureFile:
    @pytest.mark.parametrize("dataset, raw", DATASETS)
    def test_it_is_sorted_and_newline_terminated(self, dataset, raw):
        """Kept tidy so a hand-edit produces a clean, reviewable diff."""
        document = json.loads(raw)
        assert list(document) == sorted(document)
        assert raw.endswith("\n")

    def test_ids_are_unique_across_archetypes(self, by_id_records):
        ids = [r["id"] for r in by_id_records.values()]
        assert len(set(ids)) == len(ids)


# -----------------------------------------------------------------------------#
# 5. THE WALK FIXTURE — the shapes the folder walk has to survive
# -----------------------------------------------------------------------------#
class TestWalkFixtureStructure:
    def test_a_folder_spans_more_than_one_page(self, walk_records):
        """`next_page` on page 1 is what proves the pager is 1-indexed here."""
        multi = [p for p in walk_records["folder_pages"].values() if len(p) > 1]
        assert multi
        assert multi[0][0]["pagination"]["next_page"] == 2
        assert multi[0][0]["pagination"]["current_page"] == 1

    def test_page_counts_add_up_to_total_results(self, walk_records):
        """Otherwise IncompleteWalkError fires on a fixture, not on a real bug."""
        for guid, pages in walk_records["folder_pages"].items():
            collected = sum(len(page["ids"]) for page in pages)
            assert collected == pages[0]["pagination"]["total_results"], guid

    def test_an_empty_folder_exists(self, walk_records):
        assert any(
            page[0]["ids"] == [] for page in walk_records["folder_pages"].values()
        )

    def test_the_trash_folder_reports_itself_as_not_trashed(self, walk_records):
        """Measured upstream: the container carries in_trash False."""
        trash = [
            i
            for i in walk_records["items"].values()
            if str(i.get("title", "")).casefold() == "trash"
        ]
        assert len(trash) == 1
        assert trash[0]["in_trash"] is False
        assert trash[0]["content_type_id"] == 10

    def test_a_trashed_protocol_exists(self, walk_records):
        assert any(
            i["in_trash"] and i["content_type_id"] == 1
            for i in walk_records["items"].values()
        )

    def test_a_trashed_folder_holds_an_unflagged_protocol(self, walk_records):
        """The one case that could not be measured: does the flag propagate?

        Upstream has no such protocol, so the fixture supplies the shape and the
        walk's answer is a decision we make, not one we observed.
        """
        folders = {
            i["guid"]: i
            for i in walk_records["items"].values()
            if i["content_type_id"] == 10 and i["in_trash"]
        }
        assert folders
        held = [
            walk_records["items"][str(item_id)]
            for guid in folders
            for page in walk_records["folder_pages"][guid]
            for item_id in page["ids"]
        ]
        assert any(i["content_type_id"] == 1 and not i["in_trash"] for i in held)

    def test_one_protocol_is_filed_in_two_folders(self, walk_records):
        seen: list[int] = []
        for pages in walk_records["folder_pages"].values():
            for page in pages:
                seen.extend(page["ids"])
        assert len(seen) != len(set(seen))

    def test_two_protocols_share_a_version_family(self, walk_records):
        families = [
            i["version_class"]
            for i in walk_records["items"].values()
            if i["content_type_id"] == 1
        ]
        assert len(families) != len(set(families))

    def test_a_non_protocol_content_item_exists(self, walk_records):
        """type_id 3 is a Collection — it must not be sealed as a protocol."""
        assert any(
            i["content_type_id"] == 1 and i["type_id"] != 1
            for i in walk_records["items"].values()
        )
