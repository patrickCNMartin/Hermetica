# -----------------------------------------------------------------------------#
# TESTS — canonical serialization + content hash
# -----------------------------------------------------------------------------#
import unicodedata

import pytest

from seal.canonical import canonical_json, content_hash

# Frozen vector. If this changes, every hash in every DB and lock file is
# invalidated — the test exists so that can never happen silently.
FROZEN_INPUT = {"b": 1, "a": [2, {"d": None, "c": True}], "e": "café"}
FROZEN_JSON = b'{"a":[2,{"c":true,"d":null}],"b":1,"e":"caf\\u00e9"}'
FROZEN_HASH = "1554fedf9a439a5ddd6a772f9e112ef427d431c484c78cbd4d0a029985733377"


# -----------------------------------------------------------------------------#
# 1. DETERMINISM
# -----------------------------------------------------------------------------#
class TestDeterminism:
    def test_key_order_is_irrelevant(self):
        assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})

    def test_nested_key_order_is_irrelevant(self):
        a = {"outer": {"z": 1, "y": {"q": 2, "p": 3}}}
        b = {"outer": {"y": {"p": 3, "q": 2}, "z": 1}}
        assert content_hash(a) == content_hash(b)

    def test_repeated_calls_agree(self):
        obj = {"x": [1, 2, {"y": "z"}]}
        assert content_hash(obj) == content_hash(obj)

    def test_no_incidental_whitespace(self):
        assert b" " not in canonical_json({"a": 1, "b": [2, 3]})

    def test_output_is_ascii(self):
        canonical_json({"t": "Protokoll — Zellkultur ≥5 µL"}).decode("ascii")


# -----------------------------------------------------------------------------#
# 2. SENSITIVITY — real differences must change the hash
# -----------------------------------------------------------------------------#
class TestSensitivity:
    def test_value_change_changes_hash(self):
        assert content_hash({"a": 1}) != content_hash({"a": 2})

    def test_list_order_is_significant(self):
        assert content_hash({"a": [1, 2]}) != content_hash({"a": [2, 1]})

    def test_int_and_float_differ(self):
        assert content_hash({"a": 1}) != content_hash({"a": 1.0})

    def test_missing_key_differs_from_null(self):
        assert content_hash({"a": 1}) != content_hash({"a": 1, "b": None})

    def test_string_number_differs_from_number(self):
        assert content_hash({"a": 1}) != content_hash({"a": "1"})


# -----------------------------------------------------------------------------#
# 3. UNICODE
# -----------------------------------------------------------------------------#
class TestUnicode:
    def test_nfc_and_nfd_collapse(self):
        """Same visible text typed on different keyboards must hash the same."""
        nfc = unicodedata.normalize("NFC", "café")
        nfd = unicodedata.normalize("NFD", "café")
        assert nfc != nfd  # genuinely different byte sequences
        assert content_hash({"t": nfc}) == content_hash({"t": nfd})

    def test_normalization_applies_to_keys(self):
        nfc = unicodedata.normalize("NFC", "protocolé")
        nfd = unicodedata.normalize("NFD", "protocolé")
        assert content_hash({nfc: 1}) == content_hash({nfd: 1})

    def test_normalization_reaches_nested_values(self):
        nfc = unicodedata.normalize("NFC", "café")
        nfd = unicodedata.normalize("NFD", "café")
        assert content_hash({"a": [{"b": nfc}]}) == content_hash({"a": [{"b": nfd}]})

    def test_distinct_text_still_differs(self):
        assert content_hash({"t": "café"}) != content_hash({"t": "cafe"})


# -----------------------------------------------------------------------------#
# 4. REJECTED INPUT — fail loudly
# -----------------------------------------------------------------------------#
class TestRejectedInput:
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_floats_raise(self, bad):
        """NaN/Infinity are not valid JSON — never let them reach a hash."""
        with pytest.raises(ValueError):
            canonical_json({"a": bad})

    def test_unserializable_type_raises(self):
        with pytest.raises(TypeError):
            canonical_json({"a": object()})


# -----------------------------------------------------------------------------#
# 5. THE FROZEN CONTRACT
# -----------------------------------------------------------------------------#
class TestFrozenContract:
    def test_serialization_is_frozen(self):
        assert canonical_json(FROZEN_INPUT) == FROZEN_JSON

    def test_hash_is_frozen(self):
        assert content_hash(FROZEN_INPUT) == FROZEN_HASH

    def test_hash_shape(self):
        h = content_hash({"a": 1})
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)
