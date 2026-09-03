# -----------------------------------------------------------------------------#
# TESTS — the pull log
# -----------------------------------------------------------------------------#
"""Some of what a pull decides leaves no trace in the store — a protocol held
back for a keyword, one admitted by the family clause, a warning nobody acted
on. Without the log those decisions are unauditable after the fact."""

import json

from chronos.pull_log import PULL_LOG_NAME, log_path, read_pulls, record_pull
from utils.dates import as_iso


class TestRecordPull:
    def test_it_creates_the_log_on_the_first_pull(self, tmp_path):
        path = record_pull(str(tmp_path), 1700000000, {"selected": 3})

        assert path == tmp_path / PULL_LOG_NAME
        assert path.exists()

    def test_it_creates_the_db_directory_if_absent(self, tmp_path):
        target = tmp_path / "fresh" / "db"

        record_pull(str(target), 1700000000, {"selected": 1})

        assert (target / PULL_LOG_NAME).exists()

    def test_a_second_pull_appends_rather_than_replacing(self, tmp_path):
        record_pull(str(tmp_path), 1700000000, {"selected": 1})
        record_pull(str(tmp_path), 1700000060, {"selected": 2})

        pulls = read_pulls(str(tmp_path))

        assert [p["selected"] for p in pulls] == [1, 2]

    def test_every_entry_carries_its_timestamp_both_ways(self, tmp_path):
        record_pull(str(tmp_path), 1700000000, {"selected": 1})

        entry = read_pulls(str(tmp_path))[0]

        assert entry["pulled_at"] == 1700000000
        assert entry["pulled_at_iso"] == as_iso(1700000000)

    def test_one_line_per_pull(self, tmp_path):
        """Line-delimited so a truncated write costs one record, not the file."""
        for n in range(3):
            record_pull(str(tmp_path), 1700000000 + n, {"selected": n})

        lines = log_path(str(tmp_path)).read_text().splitlines()

        assert len(lines) == 3
        assert all(json.loads(line) for line in lines)

    def test_the_caller_payload_is_preserved(self, tmp_path):
        detail = {
            "strategy": "walk",
            "diff": {"new": [112516], "changed": []},
            "warnings": ["something worth reading later"],
        }

        record_pull(str(tmp_path), 1700000000, detail)

        assert read_pulls(str(tmp_path))[0]["diff"] == detail["diff"]


class TestReadPulls:
    def test_no_log_yet_reads_as_no_pulls(self, tmp_path):
        assert read_pulls(str(tmp_path)) == []
