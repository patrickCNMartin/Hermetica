# -----------------------------------------------------------------------------#
# TESTS — signed-URL scrubbing before hashing
# -----------------------------------------------------------------------------#
from seal.contract import (
    hashable_content,
    protocol_hash,
    scrub_signed_urls,
)

# Real payload from protocol 114262 (an image attached inside materials_text).
# Two consecutive pulls 34 seconds apart; only X-Amz-Date and X-Amz-Signature
# differ. Verbatim so a regex change that stops matching this is caught.
REAL_A = (
    "https://protocols-files.s3.amazonaws.com/files/ssyscbbv7.png"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    "\\u0026X-Amz-Credential=AKIAWFTFYUBUZ2U2JGOS%2F20260727%2Fus-east-1%2Fs3%2F"
    "aws4_request"
    "\\u0026X-Amz-Date=20260727T133635Z"
    "\\u0026X-Amz-Expires=604800"
    "\\u0026X-Amz-SignedHeaders=host"
    "\\u0026X-Amz-Signature="
    "4f6ce7d185d98044d8d95ad37a2756b5a07bd355cc361f21b9fcb596df2c24bc"
    '","name":"Cp02.Png"'
)
REAL_B = REAL_A.replace("20260727T133635Z", "20260727T133709Z").replace(
    "4f6ce7d185d98044d8d95ad37a2756b5a07bd355cc361f21b9fcb596df2c24bc",
    "716127447edf47fd0c9bfb9f88d22515eb79fb8b19f91b4b59044e7820c564eb",
)


def protocol(materials_text):
    return {"id": 1, "guid": "G", "title": "T", "description": "",
            "doi": "", "uri": "u", "guidelines": None, "materials": [],
            "materials_text": materials_text, "units": [], "warning": None}


# -----------------------------------------------------------------------------#
# 1. THE REGRESSION THAT MOTIVATED THIS
# -----------------------------------------------------------------------------#
class TestRealPayload:
    def test_rotating_signature_collapses_to_one_hash(self):
        """Two pulls of an unedited protocol must be ONE version."""
        assert REAL_A != REAL_B
        assert protocol_hash(protocol(REAL_A)) == protocol_hash(protocol(REAL_B))

    def test_signature_value_is_gone(self):
        scrubbed = scrub_signed_urls(REAL_A)
        assert "4f6ce7d185d98044" not in scrubbed
        assert "20260727T133635Z" not in scrubbed
        assert "AKIAWFTFYUBUZ2U2JGOS" not in scrubbed

    def test_the_file_identity_survives(self):
        """Scrub the credentials, keep the thing they point at."""
        scrubbed = scrub_signed_urls(REAL_A)
        assert "protocols-files.s3.amazonaws.com/files/ssyscbbv7.png" in scrubbed
        assert "Cp02.Png" in scrubbed
        assert "X-Amz-Signature=" in scrubbed   # param kept, value blanked


# -----------------------------------------------------------------------------#
# 2. STABILITY
# -----------------------------------------------------------------------------#
class TestStability:
    def test_idempotent(self):
        once = scrub_signed_urls(REAL_A)
        assert scrub_signed_urls(once) == once

    def test_reaches_nested_structures(self):
        nested = {"a": [{"b": REAL_A}]}
        assert protocol_hash(protocol(nested)) == protocol_hash(
            protocol({"a": [{"b": REAL_B}]})
        )

    def test_applies_through_hashable_content(self):
        """The blob that gets stored is the scrubbed one."""
        content = hashable_content(protocol(REAL_A))
        assert "4f6ce7d185d98044" not in content["materials_text"]

    def test_bare_ampersand_separator_also_works(self):
        """Not every embedding escapes the separator."""
        a = "http://x/f.png?X-Amz-Date=111&X-Amz-Signature=aaa&keep=yes"
        b = "http://x/f.png?X-Amz-Date=222&X-Amz-Signature=bbb&keep=yes"
        assert scrub_signed_urls(a) == scrub_signed_urls(b)
        assert "keep=yes" in scrub_signed_urls(a)

    def test_cloudfront_params(self):
        a = "http://x/f.png?Policy=AAA&Signature=BBB&Key-Pair-Id=CCC"
        b = "http://x/f.png?Policy=ZZZ&Signature=YYY&Key-Pair-Id=XXX"
        assert scrub_signed_urls(a) == scrub_signed_urls(b)


# -----------------------------------------------------------------------------#
# 3. WHAT MUST *NOT* BE TOUCHED
# -----------------------------------------------------------------------------#
class TestPrecision:
    def test_real_content_edit_still_changes_the_hash(self):
        """The whole point of hashing materials_text is to see genuine edits."""
        h1 = protocol_hash(protocol("Add 5 mL buffer."))
        h2 = protocol_hash(protocol("Add 10 mL buffer."))
        assert h1 != h2

    def test_edit_alongside_a_rotating_signature_is_still_detected(self):
        """A signature rotating must not mask a real change in the same field."""
        h1 = protocol_hash(protocol(REAL_A + " Add 5 mL buffer."))
        h2 = protocol_hash(protocol(REAL_B + " Add 10 mL buffer."))
        assert h1 != h2

    def test_prose_is_not_scrubbed(self):
        """A leading separator is required, so plain text survives."""
        text = "The reagent Expires=soon and the Policy=strict. Signature=needed."
        assert scrub_signed_urls(text) == text

    def test_unsigned_urls_are_untouched(self):
        url = "https://www.protocols.io/img/avatars/001.png?size=large&v=2"
        assert scrub_signed_urls(url) == url

    def test_non_string_types_pass_through(self):
        assert scrub_signed_urls(42) == 42
        assert scrub_signed_urls(None) is None
        assert scrub_signed_urls(True) is True
