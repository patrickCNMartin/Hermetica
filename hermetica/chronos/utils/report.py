# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from pathlib import Path

from seal.dates import as_iso

# -----------------------------------------------------------------------------#
# CONSTANTS & STORES
# -----------------------------------------------------------------------------#
# Human-readable twin of pull_log.jsonl, overwritten each run.
PULL_REPORT_NAME = "pull_report.txt"

# Above this many, ids are counted rather than listed.
NAME_IDS_UPTO = 12

WIDTH = 66


# -----------------------------------------------------------------------------#
# FORMATTING
# -----------------------------------------------------------------------------#
def _ids(values) -> str:
    values = list(values or [])
    if not values:
        return ""
    if len(values) > NAME_IDS_UPTO:
        return f"({len(values)} ids, see the log)"
    return ", ".join(str(v) for v in values)


def _line(label: str, count, detail: str = "") -> str:
    return f"  {label:<22}{str(count):>5}   {detail}".rstrip()


def _header(entry: dict, outcome: str) -> list[str]:
    stamp = entry.get("pulled_at_iso") or as_iso(entry.get("pulled_at", 0))
    return [
        f"Hermetica pull — {stamp}",
        "=" * WIDTH,
        f"  strategy              {entry.get('strategy', 'unknown')}",
        f"  outcome               {outcome}",
    ]


def format_report(entry: dict) -> str:
    """One pull as something a person can read over coffee."""
    diff = entry.get("diff") or {}
    warnings = entry.get("warnings") or []
    deprecated = entry.get("deprecated") or []

    outcome = "DRY RUN" if entry.get("dry_run") else "OK"
    if warnings:
        outcome += f"  ({len(warnings)} warning{'s' if len(warnings) > 1 else ''})"
    lines = _header(entry, outcome)

    lines += ["", "DISCOVERY"]
    if "workspace_items" in entry:
        lines.append(_line("workspace items", entry["workspace_items"]))
    if "shared_with_user" in entry:
        lines.append(_line("shared_with_user", entry["shared_with_user"]))
    lines.append(_line("selected", entry.get("selected", 0)))
    lines.append(_line("trashed, skipped", len(entry.get("trashed") or [])))
    lines.append(
        _line("excluded", len(entry.get("excluded") or []), _ids(entry.get("excluded")))
    )
    if entry.get("degraded"):
        lines += [
            "",
            "  NOTE: the fallback strategy is incomplete by construction.",
            "  See docs/protocols_io_findings.md sections 4 and 5.",
        ]

    if not entry.get("dry_run"):
        lines += ["", "SEALED"]
        lines.append(_line("fetched", entry.get("fetched", 0)))
        lines.append(_line("deprecated tag", len(deprecated), _ids(deprecated)))
        lines.append(_line("sealed", entry.get("sealed", 0)))
        for key in ("new", "changed", "unchanged", "absent"):
            if key in diff:
                lines.append(_line(key, len(diff[key]), _ids(diff[key])))

    lines += ["", f"WARNINGS ({len(warnings)})"]
    lines += [f"  - {w}" for w in warnings] or ["  none"]
    lines += ["", "full record: db/pull_log.jsonl", ""]
    return "\n".join(lines)


def format_failure(entry: dict, error: BaseException) -> str:
    """A pull that did not finish. The store is unchanged; say so plainly."""
    lines = _header(entry, "FAILED")
    lines += [
        "",
        f"  {type(error).__name__}: {error}",
        "",
        "  The store is written once, in a single transaction at the end of a",
        "  pull, and that transaction rolls back on error — so the previous",
        "  night's state still stands. The next run will retry.",
        "",
        "full record: db/pull_log.jsonl",
        "",
    ]
    return "\n".join(lines)


# -----------------------------------------------------------------------------#
# WRITE
# -----------------------------------------------------------------------------#
def report_path(db_dir: str) -> Path:
    return Path(db_dir) / PULL_REPORT_NAME


def write_report(db_dir: str, text: str) -> Path:
    """Replace the report with this run's. History lives in the jsonl log."""
    path = report_path(db_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
