# -----------------------------------------------------------------------------#
# TESTS — the human-readable pull report
# -----------------------------------------------------------------------------#
"""Two failures matter here and neither is cosmetic: a report that omits the
warnings, and a failed pull that produces no report at all. A nightly job that
stops running silently is the outcome this exists to prevent."""

import pytest

from chronos.utils.report import (
    PULL_REPORT_NAME,
    format_failure,
    format_report,
    report_path,
    write_report,
)


@pytest.fixture
def entry():
    """A successful walk pull, shaped like what chronos actually logs."""
    return {
        "pulled_at": 1700000000,
        "pulled_at_iso": "2023-11-14T22:13:20+00:00",
        "strategy": "walk",
        "workspace_items": 67,
        "selected": 57,
        "trashed": list(range(200, 209)),
        "excluded": [106],
        "fetched": 57,
        "deprecated": [],
        "sealed": 57,
        "diff": {
            "new": [1, 2, 3],
            "changed": [],
            "unchanged": list(range(10, 62)),
            "absent": list(range(200, 209)),
        },
        "warnings": [],
    }


# -----------------------------------------------------------------------------#
# 1. A GOOD PULL
# -----------------------------------------------------------------------------#
class TestFormatReport:
    def test_it_leads_with_when_and_how(self, entry):
        text = format_report(entry)

        assert "2023-11-14T22:13:20+00:00" in text
        assert "walk" in text
        assert "OK" in text

    def test_the_headline_counts_are_present(self, entry):
        text = format_report(entry)

        for label in ("workspace items", "selected", "sealed", "trashed, skipped"):
            assert label in text

    def test_short_id_lists_are_named(self, entry):
        """One excluded id is worth reading; you cannot act on a count."""
        text = format_report(entry)

        assert "106" in text
        assert "1, 2, 3" in text

    def test_long_id_lists_are_counted_not_dumped(self, entry):
        """52 unchanged ids would bury the three that moved."""
        text = format_report(entry)

        assert "(52 ids, see the log)" in text

    def test_no_warnings_says_none_rather_than_nothing(self, entry):
        text = format_report(entry)

        assert "WARNINGS (0)" in text
        assert "none" in text

    def test_warnings_are_reproduced_in_full(self, entry):
        entry["warnings"] = ["in_trash flag disagrees for 108", "type_id 3 is not"]

        text = format_report(entry)

        assert "WARNINGS (2)" in text
        assert "in_trash flag disagrees for 108" in text
        assert "type_id 3 is not" in text

    def test_warnings_reach_the_outcome_line(self, entry):
        """Buried at the bottom they get skimmed past."""
        entry["warnings"] = ["something happened"]

        assert "1 warning" in format_report(entry).splitlines()[3]

    def test_a_dry_run_says_so_and_skips_the_sealed_block(self, entry):
        dry = {k: v for k, v in entry.items() if k not in ("diff", "sealed", "fetched")}
        dry["dry_run"] = True

        text = format_report(dry)

        assert "DRY RUN" in text
        assert "SEALED" not in text

    def test_the_degraded_fallback_is_called_out(self, entry):
        entry["degraded"] = True

        text = format_report(entry)

        assert "incomplete by construction" in text
        assert "protocols_io_findings" in text

    def test_a_walk_pull_does_not_claim_to_be_degraded(self, entry):
        assert "incomplete by construction" not in format_report(entry)

    def test_deprecated_protocols_are_named(self, entry):
        entry["deprecated"] = [4242]

        assert "4242" in format_report(entry)


# -----------------------------------------------------------------------------#
# 2. A FAILED PULL
# -----------------------------------------------------------------------------#
class TestFormatFailure:
    def test_it_names_the_error_and_its_type(self):
        entry = {"pulled_at": 1700000000, "strategy": "walk"}

        text = format_failure(entry, ValueError("folder guid was rejected"))

        assert "FAILED" in text
        assert "ValueError" in text
        assert "folder guid was rejected" in text

    def test_it_states_that_the_store_is_untouched(self):
        """The reader's first question is whether the data is now wrong."""
        text = format_failure({"pulled_at": 1700000000}, RuntimeError("boom"))

        assert "rolls back on error" in text
        assert "previous" in text

    def test_it_works_without_an_iso_stamp(self):
        """The failure path builds its entry by hand; it may be sparse."""
        assert "1970" in format_failure({"pulled_at": 0}, RuntimeError("boom"))


# -----------------------------------------------------------------------------#
# 3. WRITING IT
# -----------------------------------------------------------------------------#
class TestWriteReport:
    def test_it_writes_to_the_db_directory(self, tmp_path, entry):
        path = write_report(str(tmp_path), format_report(entry))

        assert path == tmp_path / PULL_REPORT_NAME
        assert "Hermetica pull" in path.read_text()

    def test_it_creates_the_directory_if_absent(self, tmp_path, entry):
        target = tmp_path / "fresh" / "db"

        write_report(str(target), format_report(entry))

        assert report_path(str(target)).exists()

    def test_the_latest_run_replaces_the_last(self, tmp_path, entry):
        """History is the jsonl log's job; this file answers 'what just happened'."""
        write_report(str(tmp_path), "first")
        write_report(str(tmp_path), "second")

        assert report_path(str(tmp_path)).read_text() == "second"
