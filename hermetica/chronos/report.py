# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from utils.dates import as_iso

# -----------------------------------------------------------------------------#
# CONSTANTS & STORES
# -----------------------------------------------------------------------------#
# Human-readable twin of pull_log.jsonl, overwritten each run.
PULL_REPORT_NAME = "pull_report.txt"

# Above this many, ids are counted rather than listed.
NAME_IDS_UPTO = 12

WIDTH = 66

# Where the message goes when no relay is configured.
DRAFT_NAME = "last_report_email.eml"

# ASCII: an em dash here arrives base64-wrapped by RFC 2047, which defeats
# any mail filter matching on the subject.
SUBJECT_OK = "Hermetica pull OK: {stamp}"
SUBJECT_FAILED = "Hermetica pull FAILED: {stamp}"


# ---------------------------------------------------------------------------#
# REPORT ERRORS
# ---------------------------------------------------------------------------#
class MailNotSentError(RuntimeError):
    """The report was built but the transport refused it."""

    def __init__(self, error: Exception, draft: str):
        self.error, self.draft = error, draft
        super().__init__(f"{type(error).__name__}: {error} — message kept at {draft}")


# -----------------------------------------------------------------------------#
# FORMATTING
# -----------------------------------------------------------------------------#
def ids(values) -> str:
    values = list(values or [])
    if not values:
        return ""
    if len(values) > NAME_IDS_UPTO:
        return f"({len(values)} ids, see the log)"
    return ", ".join(str(v) for v in values)


def line(label: str, count, detail: str = "") -> str:
    return f"  {label:<22}{str(count):>5}   {detail}".rstrip()


def header(entry: dict, outcome: str) -> list[str]:
    stamp = entry.get("pulled_at_iso") or as_iso(entry.get("pulled_at", 0))
    lines = [f"Hermetica pull — {stamp}", "=" * WIDTH]
    # A run may cover several sources; naming it is what keeps them apart.
    if entry.get("source"):
        lines.append(f"  source                {entry['source']}")
    lines.append(f"  outcome               {outcome}")
    return lines


def format_report(entry: dict) -> str:
    """One pull as something a person can read over coffee."""
    diff = entry.get("diff") or {}
    warnings = entry.get("warnings") or []
    deprecated = entry.get("deprecated") or []

    outcome = "OK"
    if warnings:
        outcome += f"  ({len(warnings)} warning{'s' if len(warnings) > 1 else ''})"
    lines = header(entry, outcome)

    lines += ["", "DISCOVERY"]
    if "workspace_items" in entry:
        lines.append(line("workspace items", entry["workspace_items"]))
    lines.append(line("selected", entry.get("selected", 0)))
    lines.append(line("trashed, skipped", len(entry.get("trashed") or [])))
    lines.append(
        line("excluded", len(entry.get("excluded") or []), ids(entry.get("excluded")))
    )

    lines += ["", "SEALED"]
    lines.append(line("fetched", entry.get("fetched", 0)))
    lines.append(line("deprecated tag", len(deprecated), ids(deprecated)))
    lines.append(line("sealed", entry.get("sealed", 0)))
    for key in ("new", "changed", "unchanged", "absent"):
        if key in diff:
            lines.append(line(key, len(diff[key]), ids(diff[key])))

    lines += ["", f"WARNINGS ({len(warnings)})"]
    lines += [f"  - {w}" for w in warnings] or ["  none"]
    lines += ["", "full record: db/pull_log.jsonl", ""]
    return "\n".join(lines)


def format_failure(entry: dict, error: BaseException) -> str:
    """A pull that did not finish. The store is unchanged; say so plainly."""
    lines = header(entry, "FAILED")
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


# -----------------------------------------------------------------------------#
# SEND
# -----------------------------------------------------------------------------#
def recipients(raw: str) -> list[str]:
    """EMAIL may name several maintainers, comma separated."""
    return [address.strip() for address in raw.split(",") if address.strip()]


def build_email(
    to: list[str],
    sender: str,
    body: str,
    stamp: str,
    failed: bool = False,
) -> EmailMessage:
    """The report as a message. The subject carries the outcome."""
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(to)
    message["Subject"] = (SUBJECT_FAILED if failed else SUBJECT_OK).format(stamp=stamp)
    message.set_content(body)
    return message


def draft_path(db_dir: str) -> Path:
    return Path(db_dir) / DRAFT_NAME


def write_draft(db_dir: str, message: EmailMessage) -> Path:
    """Drop the message on disk, headers and all."""
    path = draft_path(db_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(message))
    return path


def send_email(
    message: EmailMessage,
    host: str,
    port: int = 587,
    user: str = "",
    password: str = "",
    security: str = "starttls",
) -> None:
    """Hand the message to a relay: TLS on 465, STARTTLS on 587, or plain."""
    security = security.lower()
    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(host, port)

    with server:
        if security == "starttls":
            server.starttls(context=ssl.create_default_context())
        # A relay that authenticates by IP takes no credentials.
        if user:
            server.login(user, password)
        server.send_message(message)


def send_report(
    db_dir: str,
    text: str,
    pulled_at: int,
    to: str,
    sender: str = "",
    host: str = "",
    port: int = 587,
    user: str = "",
    password: str = "",
    security: str = "starttls",
    failed: bool = False,
) -> str:
    """Mail the report, or draft it to disk when there is nowhere to send it."""
    addresses = recipients(to)
    if not addresses:
        return "  no EMAIL configured — report not mailed"

    message = build_email(
        addresses,
        sender or user or addresses[0],
        text,
        as_iso(pulled_at),
        failed=failed,
    )

    if not host:
        path = write_draft(db_dir, message)
        return f"  no SMTP_HOST — message drafted to {path}"

    try:
        send_email(message, host, port, user, password, security)
    except (OSError, smtplib.SMTPException) as error:
        # Keep the message: a send that failed is the case where its contents
        # matter most.
        path = write_draft(db_dir, message)
        raise MailNotSentError(error, path) from error

    return f"  report mailed to {', '.join(addresses)} via {host}:{port}"
