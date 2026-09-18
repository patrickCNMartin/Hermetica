# -----------------------------------------------------------------------------#
# TESTS — epoch <-> human conversion at the call boundary
# -----------------------------------------------------------------------------#
from datetime import date, datetime, timezone

import pytest

from utils.dates import (
    as_iso,
    from_epoch,
    get_timestamp,
    to_epoch,
)

EPOCH = 1745934254  # 2025-04-29T13:44:14Z
DAY_START = 1745884800  # 2025-04-29T00:00:00Z
DAY_END = 1745971199  # 2025-04-29T23:59:59Z


class TestToEpoch:
    @pytest.mark.parametrize(
        "value",
        [
            EPOCH,
            float(EPOCH),
            "2025-04-29T13:44:14+00:00",
            "2025-04-29T13:44:14Z",
            datetime(2025, 4, 29, 13, 44, 14, tzinfo=timezone.utc),
        ],
    )
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

    def test_as_iso(self):
        assert as_iso(EPOCH) == "2025-04-29T13:44:14+00:00"


class TestNow:
    def test_now_is_an_int_and_plausible(self):
        n = get_timestamp()
        assert isinstance(n, int)
        assert n > 1_700_000_000
