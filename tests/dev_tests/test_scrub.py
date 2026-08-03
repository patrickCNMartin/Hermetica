# -----------------------------------------------------------------------------#
# TESTS — signed-URL scrubbing before hashing
# -----------------------------------------------------------------------------#
"""The regression these guard: attachments carry AWS/CloudFront signing params
that upstream regenerates on every request. Left in, an untouched protocol forks
a new version on every nightly pull — measured at two hashes 34 s apart."""

import json
import re

from seal.contract import build_protocol_artefact, protocol_hash, scrub_signed_urls

# Rotate the values a real re-request would change, leaving everything else alone.
_ROTATE = re.compile(
    r"((?:X-Amz-Signature|X-Amz-Date|X-Amz-Credential|Policy|Signature)=)"
    r"([0-9a-zA-Z%/_-]+)",
    re.IGNORECASE,
)


def rotate(value):
    """Simulate a second pull: same content, freshly minted signatures."""
    if isinstance(value, str):
        return _ROTATE.sub(lambda m: m.group(1) + "f" * len(m.group(2)), value)
    if isinstance(value, dict):
        return {k: rotate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [rotate(v) for v in value]
    return value


# -----------------------------------------------------------------------------#
# 1. THE REGRESSION THAT MOTIVATED THIS
# -----------------------------------------------------------------------------#
class TestRotatingSignatures:
    def test_rotating_signature_collapses_to_one_hash(self, record):
        """Two pulls of an unedited protocol must be ONE version."""
        signed = record("signed_urls")
        rotated = rotate(signed)
        assert rotated != signed  # the payloads genuinely differ
        assert protocol_hash(build_protocol_artefact(signed)) == protocol_hash(
            build_protocol_artefact(rotated)
        )

    def test_signature_values_are_gone(self, record):
        signed = record("signed_urls")
        scrubbed = json.dumps(scrub_signed_urls(signed))
        assert not re.search(r"X-Amz-Signature=[^\"&\\\s]", scrubbed)
        assert not re.search(r"X-Amz-Date=[^\"&\\\s]", scrubbed)
        assert not re.search(r"X-Amz-Credential=[^\"&\\\s]", scrubbed)

    def test_the_file_identity_survives(self, record):
        """Scrub the credentials, keep the thing they point at."""
        signed = record("signed_urls")
        document = signed["documents"][0]
        scrubbed = scrub_signed_urls(document["url"])
        host = document["url"].split("?")[0]
        assert host in scrubbed  # URL and filename still hashed
        assert "X-Amz-Signature=" in scrubbed  # param kept, value blanked

    def test_swapping_the_attached_file_is_a_version_change(self, record):
        """The URL stays in the hash, so a different file is different content.

        The hashed attachments are the ones embedded in rich text; the top-level
        `documents` array is outside HASH_FIELDS and deliberately invisible.
        """
        signed = record("signed_urls")
        before = protocol_hash(build_protocol_artefact(signed))
        signed["materials_text"] = signed["materials_text"].replace(
            ".example.org/", ".example.org/renamed-"
        )
        assert protocol_hash(build_protocol_artefact(signed)) != before

    def test_the_documents_array_is_outside_the_hash(self, record):
        """It is not in HASH_FIELDS — editing it must be invisible to versioning."""
        signed = record("signed_urls")
        before = protocol_hash(build_protocol_artefact(signed))
        signed["documents"][0]["url"] = "https://elsewhere.example.org/x.pdf"
        assert protocol_hash(build_protocol_artefact(signed)) == before


# -----------------------------------------------------------------------------#
# 2. BOTH EMBEDDING FORMS — the fixture must keep carrying them
# -----------------------------------------------------------------------------#
class TestSeparatorForms:
    def test_rich_text_uses_the_escaped_separator(self, record):
        """Inside a double-encoded document the separator is the literal \\u0026."""
        signed = record("signed_urls")
        assert "\\u0026" in signed["materials_text"]
        assert "X-Amz-Signature" in signed["materials_text"]

    def test_documents_use_a_bare_separator(self, record):
        """A plain URL field escapes nothing — the regex must match both."""
        signed = record("signed_urls")
        url = signed["documents"][0]["url"]
        assert "&X-Amz" in url and "\\u0026" not in url

    def test_escaped_separator_is_scrubbed(self, record):
        signed = record("signed_urls")
        scrubbed = scrub_signed_urls(signed["materials_text"])
        assert not re.search(r"X-Amz-Signature=[0-9a-f]", scrubbed)
        assert "\\u0026X-Amz-Signature=" in scrubbed  # structure survives

    def test_bare_separator_is_scrubbed(self, record):
        signed = record("signed_urls")
        scrubbed = scrub_signed_urls(signed["documents"][0]["url"])
        assert not re.search(r"X-Amz-Signature=[0-9a-f]", scrubbed)
        assert "&X-Amz-Signature=" in scrubbed


# -----------------------------------------------------------------------------#
# 3. STABILITY
# -----------------------------------------------------------------------------#
class TestStability:
    def test_idempotent(self, record):
        signed = record("signed_urls")
        once = scrub_signed_urls(signed["materials_text"])
        assert scrub_signed_urls(once) == once

    def test_reaches_nested_structures(self):
        a = {"a": [{"b": "http://x/f.png?X-Amz-Date=111&X-Amz-Signature=aaa"}]}
        b = {"a": [{"b": "http://x/f.png?X-Amz-Date=222&X-Amz-Signature=bbb"}]}
        assert scrub_signed_urls(a) == scrub_signed_urls(b)

    def test_applies_through_the_artefact(self, record):
        """The blob that gets stored is the scrubbed one."""
        signed = record("signed_urls")
        content = build_protocol_artefact(signed).hashable()
        assert not re.search(r"X-Amz-Signature=[0-9a-f]", content["materials"])

    def test_cloudfront_params(self):
        a = "http://x/f.png?Policy=AAA&Signature=BBB&Key-Pair-Id=CCC"
        b = "http://x/f.png?Policy=ZZZ&Signature=YYY&Key-Pair-Id=XXX"
        assert scrub_signed_urls(a) == scrub_signed_urls(b)


# -----------------------------------------------------------------------------#
# 4. WHAT MUST *NOT* BE TOUCHED
# -----------------------------------------------------------------------------#
class TestPrecision:
    def test_real_content_edit_still_changes_the_hash(self, record):
        """The whole point of hashing materials is to see genuine edits."""
        signed = record("signed_urls")
        before = protocol_hash(build_protocol_artefact(signed))
        signed["materials_text"] = signed["materials_text"].replace(
            '"text":"', '"text":"Add 10 mL buffer. ', 1
        )
        assert protocol_hash(build_protocol_artefact(signed)) != before

    def test_edit_alongside_a_rotating_signature_is_still_detected(self, record):
        """A signature rotating must not mask a real change in the same field."""
        signed = record("signed_urls")
        edited = rotate(signed)
        edited["materials_text"] = edited["materials_text"].replace(
            '"text":"', '"text":"Add 10 mL buffer. ', 1
        )
        assert protocol_hash(build_protocol_artefact(signed)) != protocol_hash(
            build_protocol_artefact(edited)
        )

    def test_prose_is_not_scrubbed(self):
        """A leading separator is required, so plain text survives."""
        text = "The reagent Expires=soon and the Policy=strict. Signature=needed."
        assert scrub_signed_urls(text) == text

    def test_unsigned_urls_are_untouched(self):
        url = "https://www.example.org/img/avatars/001.png?size=large&v=2"
        assert scrub_signed_urls(url) == url

    def test_non_string_types_pass_through(self):
        assert scrub_signed_urls(42) == 42
        assert scrub_signed_urls(None) is None
        assert scrub_signed_urls(True) is True
