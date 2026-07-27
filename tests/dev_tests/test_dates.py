# -----------------------------------------------------------------------------#
# TESTS — epoch <-> human conversion at the call boundary
# -----------------------------------------------------------------------------#
from datetime import date, datetime, timezone

import pytest

from seal.dates import (
    as_date,
    as_iso,
    end_of_day,
    from_epoch,
    now_epoch,
    to_epoch,
)

EPOCH = 1745934254                    # 2025-04-29T13:44:14Z
DAY_START = 1745884800                # 2025-04-29T00:00:00Z
DAY_END = 1745971199                  # 2025-04-29T23:59:59Z


class TestToEpoch:
    @pytest.mark.parametrize("value", [
        EPOCH,
        float(EPOCH),
        "2025-04-29T13:44:14+00:00",
        "2025-04-29T13:44:14Z",
        datetime(2025, 4, 29, 13, 44, 14, tzinfo=timezone.utc),
    ])
    def test_equivalent_forms_agree(self, value):
        assert to_epoch(value) == EPOCH

    def test_bare_date_lands_on_midnight(self):
        assert to_epoch(date(2025, 4, 29)) == DAY_START
        assert to_epoch("2025-04-29") == DAY_START

    def test_naive_datetime_is_read_as_utc(self):
        naive = datetime(2025, 4, 29, 13, 44, 14)
        assert to_epoch(naive) == EPOCH

    def test_non_utc_offset_is_respected(self):
        """A +02:00 wall clock is two hours earlier in UTC."""
        assert to_epoch("2025-04-29T15:44:14+02:00") == EPOCH

    def test_bool_is_rejected(self):
        """bool is an int subclass — True must not silently become epoch 1."""
        with pytest.raises(TypeError):
            to_epoch(True)

    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError):
            to_epoch({"not": "a date"})


class TestFromEpoch:
    def test_round_trip(self):
        assert to_epoch(from_epoch(EPOCH)) == EPOCH

    def test_result_is_utc_aware(self):
        assert from_epoch(EPOCH).tzinfo is not None

    def test_as_date(self):
        assert as_date(EPOCH) == "2025-04-29"

    def test_as_iso(self):
        assert as_iso(EPOCH) == "2025-04-29T13:44:14+00:00"


class TestEndOfDay:
    def test_last_instant_of_the_day(self):
        assert end_of_day(EPOCH) == DAY_END
        assert as_date(end_of_day(EPOCH)) == "2025-04-29"

    def test_accepts_a_date_string(self):
        assert end_of_day("2025-04-29") == DAY_END

    def test_bounds_the_whole_day(self):
        """'As of date D' must include everything stamped during D."""
        assert to_epoch("2025-04-29T00:00:00Z") <= end_of_day("2025-04-29")
        assert to_epoch("2025-04-29T23:59:59Z") <= end_of_day("2025-04-29")
        assert to_epoch("2025-04-30T00:00:00Z") > end_of_day("2025-04-29")


class TestNow:
    def test_now_is_an_int_and_plausible(self):
        n = now_epoch()
        assert isinstance(n, int)
        assert n > 1_700_000_000
