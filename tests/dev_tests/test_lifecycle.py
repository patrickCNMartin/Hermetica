# -----------------------------------------------------------------------------#
# TESTS — the deprecated keyword
# -----------------------------------------------------------------------------#
"""Matching is exact against an allowlist of spellings. A fuzzy matcher would
accept one typo and reject the next with nothing to say which happened, so a
near miss is reported instead — `depricated` is loud rather than silently live."""

import pytest

from chronos.chronos import screen_deprecated
from seal.lifecycle import (
    DEPRECATED_TOKENS,
    is_deprecated,
    near_miss_tokens,
    split_keywords,
)


# -----------------------------------------------------------------------------#
# 1. SPLITTING
# -----------------------------------------------------------------------------#
class TestSplitKeywords:
    @pytest.mark.parametrize("value", [None, "", "   ", ",", " , , "])
    def test_empty_input_yields_nothing(self, value):
        assert split_keywords(value) == []

    def test_it_splits_trims_and_casefolds(self):
        assert split_keywords(" SP3 , Digestion ,proteomics") == [
            "sp3",
            "digestion",
            "proteomics",
        ]

    def test_a_multiword_keyword_stays_whole(self):
        """Upstream keywords are phrases — splitting on spaces would shred them."""
        assert split_keywords("sample preparation, sp3") == [
            "sample preparation",
            "sp3",
        ]


# -----------------------------------------------------------------------------#
# 2. THE PREDICATE
# -----------------------------------------------------------------------------#
class TestIsDeprecated:
    @pytest.mark.parametrize("token", sorted(DEPRECATED_TOKENS))
    def test_every_listed_spelling_is_accepted(self, token):
        assert is_deprecated(f"sp3, {token}, proteomics")

    @pytest.mark.parametrize("token", ["DEPRECATED", " Deprecated "])
    def test_case_and_padding_do_not_matter(self, token):
        assert is_deprecated(token)

    @pytest.mark.parametrize(
        "keywords",
        [None, "", "sp3, digestion", "deprecation policy", "not deprecated"],
    )
    def test_ordinary_keywords_are_not_a_flag(self, keywords):
        assert not is_deprecated(keywords)

    def test_a_substring_is_not_a_match(self):
        """`undeprecated` is not `deprecated`; the comparison is on whole tokens."""
        assert not is_deprecated("undeprecated")

    def test_the_known_misspellings_are_deliberate(self):
        """They are aliased on purpose, not tolerated by a fuzzy matcher."""
        assert {"depreciated", "depreceated"} <= DEPRECATED_TOKENS


# -----------------------------------------------------------------------------#
# 3. NEAR MISSES — reported, never acted on
# -----------------------------------------------------------------------------#
class TestNearMiss:
    def test_an_unlisted_misspelling_is_reported(self):
        assert near_miss_tokens("sp3, depricated") == ["depricated"]

    def test_a_near_miss_does_not_deprecate(self):
        """The whole point: it warns, the protocol stays live."""
        assert not is_deprecated("depricated")

    def test_a_listed_spelling_is_not_a_near_miss(self):
        assert near_miss_tokens("deprecated") == []

    def test_ordinary_keywords_are_quiet(self):
        assert near_miss_tokens("sp3, digestion, sample preparation") == []


# -----------------------------------------------------------------------------#
# 4. SCREENING A PULL
# -----------------------------------------------------------------------------#
class TestScreenDeprecated:
    def test_a_flagged_protocol_is_held_back(self):
        protocols = [
            {"id": 1, "keywords": "sp3"},
            {"id": 2, "keywords": "sp3, deprecated"},
        ]

        screened = screen_deprecated(protocols)

        assert [p["id"] for p in screened.kept] == [1]
        assert [p["id"] for p in screened.deprecated] == [2]

    def test_a_missing_keywords_field_is_not_a_flag(self):
        screened = screen_deprecated([{"id": 1}])

        assert [p["id"] for p in screened.kept] == [1]

    def test_a_near_miss_is_kept_and_warned_about(self):
        screened = screen_deprecated([{"id": 7, "keywords": "depricated"}])

        assert [p["id"] for p in screened.kept] == [7]
        assert any("depricated" in w and "7" in w for w in screened.warnings)

    def test_nothing_flagged_means_nothing_dropped(self):
        protocols = [{"id": n, "keywords": "sp3"} for n in range(5)]

        screened = screen_deprecated(protocols)

        assert len(screened.kept) == 5
        assert screened.deprecated == []
        assert screened.warnings == []
