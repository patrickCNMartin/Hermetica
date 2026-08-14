# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import html
import json
import os
import re
from collections.abc import Sequence

from scribe.richtext import render_document
from seal.dates import as_iso
from seal.seal import manifest_hash
from seal.store import active_hashes, connect, get_content

# The entry point loads env/.env; a public view prefix is not a credential, so
# unlike BASE_URL this carries a working default.
VIEW_URL = os.getenv("VIEW_URL", "https://www.protocols.io/view/")

DISPLAY_FIELDS: tuple[str, ...] = ("title", "doi", "reserved_doi", "uri")

# Fonts only; the engine is a pandoc option, not a metadata variable.
PDF_METADATA: dict[str, str] = {
    "mainfont": "DejaVu Serif",
    "sansfont": "DejaVu Sans",
    "monofont": "DejaVu Sans Mono",
}


class OrderError(ValueError):
    """`order` is not an exact permutation of the lock's pin set."""


class UnrenderableProtocolError(ValueError):
    """A protocol must be inlined but its body is in neither the lock nor the DB."""


# -----------------------------------------------------------------------------#
# RESOLUTION
# -----------------------------------------------------------------------------#
def resolve_order(entries: dict, order: Sequence[str] | None) -> list[str]:
    """Validate the caller's order. A subset would silently drop a protocol."""
    if order is None:
        return sorted(entries)
    order = [str(pid) for pid in order]
    if len(set(order)) != len(order):
        raise OrderError("order repeats a protocol_id")
    missing, unknown = (
        sorted(set(entries) - set(order)),
        sorted(set(order) - set(entries)),
    )
    if missing or unknown:
        raise OrderError(
            "order must cover the pin set exactly; "
            f"missing {missing}, unknown {unknown}"
        )
    return order


def linkable(entries: dict, db: str | None) -> set[str]:
    """Pins that are still the live version — those are the ones worth linking.

    A pin that is merely *some* active version is not enough: the link would show
    today's content while the entry states the pinned hash.
    """
    if not db:
        return set()
    with connect(db, read_only=True) as conn:
        active = active_hashes(conn)
    return {pid for pid, entry in entries.items() if active.get(pid) == entry["hash"]}


def collect_bodies(
    lock: dict, entries: dict, inline: Sequence[str], db: str | None
) -> dict:
    """Blobs for the protocols that actually inline — fetched no earlier."""
    bodies = dict(lock.get("bodies") or {})
    missing = [
        entries[pid]["hash"] for pid in inline if entries[pid]["hash"] not in bodies
    ]
    if not missing:
        return bodies
    if not db:
        raise UnrenderableProtocolError(
            f"{len(missing)} protocol(s) must be inlined but the lock carries no "
            "bodies and no database was given"
        )
    for row in get_content(db, missing):
        bodies[row.hash] = json.loads(row.protocol)
    return bodies


def collect_display(
    lock: dict, entries: dict, order: Sequence[str], bodies: dict, db: str | None
) -> dict:
    """Title and identifiers per protocol, from the cheapest source that has them."""
    display = {
        pid: dict(fields) for pid, fields in (lock.get("protocols") or {}).items()
    }
    unresolved = []
    for pid in order:
        if pid in display:
            continue
        body = bodies.get(entries[pid]["hash"])
        if body:
            display[pid] = {field: body.get(field) for field in DISPLAY_FIELDS}
        else:
            unresolved.append(pid)

    if unresolved:
        if not db:
            raise UnrenderableProtocolError(
                "a pins-only lock carries no titles; rendering one needs a database"
            )
        rows = get_content(
            db, [entries[pid]["hash"] for pid in unresolved], with_blob=False
        )
        for pid, row in zip(unresolved, rows):
            display[pid] = {
                "title": row.title,
                "doi": row.doi,
                "reserved_doi": row.reserved_doi,
                "uri": row.uri,
                "created_on": as_iso(row.created_on) if row.created_on else None,
                "creator": json.loads(row.creator) if row.creator else None,
                "authors": json.loads(row.authors) if row.authors else None,
            }
    return display


# -----------------------------------------------------------------------------#
# RENDER
# -----------------------------------------------------------------------------#
_TAGS = re.compile(r"<[^>]+>")


def plain(value: str | None) -> str:
    """Section headings arrive as HTML."""
    return html.unescape(_TAGS.sub("", value or "")).strip()


def _people(value) -> str:
    if not value:
        return ""
    names = [person.get("name") or "" for person in value if isinstance(person, dict)]
    return ", ".join(name for name in names if name)


def _facts(pid: str, entry: dict, fields: dict, body: dict | None) -> list[str]:
    """The verification block under a protocol heading."""
    rows = [
        ("protocol_id", pid),
        ("guid", entry.get("guid")),
        ("hash", entry.get("hash")),
    ]
    if body:
        rows += [("version_class", body.get("version_class"))]
    creator = fields.get("creator") or {}
    rows += [
        ("doi", fields.get("doi")),
        ("reserved_doi", fields.get("reserved_doi")),
        ("created_on", fields.get("created_on")),
        ("creator", _people([creator])),
        ("affiliation", creator.get("affiliation")),
        ("authors", _people(fields.get("authors"))),
    ]
    return [f"| {key} | {value} |" for key, value in rows if value not in (None, "")]


def _steps(body: dict) -> list[str]:
    """Steps in `chain` order, grouped under their section headings."""
    steps = {step["id"]: step for step in body.get("steps") or []}
    chain = body.get("chain") or []
    if set(chain) != set(steps):
        raise ValueError(
            f"chain does not cover steps: {sorted(set(chain) ^ set(steps))}"
        )

    units = body.get("units") or {}
    lines: list[str] = []
    section = None
    for number, step_id in enumerate(chain, start=1):
        step = steps[step_id]
        heading = plain(step.get("section"))
        if heading and heading != section:
            section = heading
            lines += ["", f"#### {heading}"]
        # A bold label rather than a list marker: steps run to several paragraphs
        # and would need continuation indenting inside an ordered list.
        flag = "  **[CRITICAL]**" if step.get("critical") else ""
        lines += ["", f"**Step {number}**{flag}", ""]
        lines.append(render_document(step.get("step"), units) or "_(no content)_")
    return lines


def _protocol(
    position: int, pid: str, entry: dict, fields: dict, body: dict | None
) -> list[str]:
    title = fields.get("title") or f"protocol {pid}"
    mode = "live" if body is None else "pinned version inlined"
    lines = [
        "",
        f"## {position}. {title} — {mode}",
        "",
        "| field | value |",
        "| --- | --- |",
    ]
    lines += _facts(pid, entry, fields, body)

    if body is None:
        uri = fields.get("uri")
        lines += (
            ["", f"Read the current protocol: <{VIEW_URL}{uri}>"]
            if uri
            else [
                "",
                "_(no uri recorded — cannot link)_",
            ]
        )
        return lines

    units = body.get("units") or {}
    for label, field in (
        ("Description", "description"),
        ("Warning", "warning"),
        ("Disclaimer", "disclaimer"),
        ("Guidelines", "guidelines"),
        ("Before you start", "before_start"),
        ("Materials", "materials"),
    ):
        rendered = render_document(body.get(field), units)
        if rendered:
            lines += ["", f"### {label}", "", rendered]
    steps = _steps(body)
    if steps:
        lines += ["", "### Steps"] + steps
    references = render_document(body.get("protocol_references"), units)
    if references:
        lines += ["", "### References", "", references]
    return lines


def _frontmatter(metadata: dict[str, str]) -> list[str]:
    """A YAML metadata block. JSON strings are valid YAML, so they quote safely."""
    if not metadata:
        return []
    return (
        ["---"]
        + [f"{key}: {json.dumps(value)}" for key, value in metadata.items()]
        + ["---", ""]
    )


def _banner(total: int, linked: int, inline: int, db: str | None) -> str:
    """State why each protocol rendered the way it did — never imply more than known."""
    if not db:
        return (
            f"**{total} protocol(s)**, all rendered in full. No database was given, "
            "so whether these pins are still the active versions is unknown."
        )
    return (
        f"**{total} protocol(s)** — {linked} rendered as links to the live protocol "
        f"(the pin is still the active version); {inline} inlined in full "
        "(the pin is no longer the active version)."
    )


def to_markdown(
    lock: dict,
    db: str | None = None,
    order: Sequence[str] | None = None,
    metadata: dict[str, str] | None = None,
) -> str:
    """Render a lock document as markdown.

    A pin that is still the live version becomes a link to protocols.io — the
    canonical copy is one click away and duplicating it here only creates a second
    thing to drift. A pin that is no longer live has no such source, so its body is
    inlined from the blob. Without a `db` that distinction is unknowable and
    everything inlines, which keeps a standalone lock renderable. `metadata` writes a
    pandoc YAML block; empty by default, so the output is plain markdown.
    """
    entries = lock["entries"]
    order = resolve_order(entries, order)
    linked = linkable(entries, db) & set(order)
    inline = [pid for pid in order if pid not in linked]

    bodies = collect_bodies(lock, entries, inline, db)
    display = collect_display(lock, entries, order, bodies, db)

    recorded = lock.get("manifest_hash")
    verified = recorded == manifest_hash(entries)
    lines = _frontmatter(metadata or {}) + [
        "# Protocol manifest",
        "",
        f"- **manifest_hash**: `{recorded}`"
        f"{'' if verified else ' — **DOES NOT MATCH the pin set**'}",
        f"- **as_of**: {lock.get('as_of')}",
        f"- **created_at**: {lock.get('created_at')}",
        "",
        _banner(len(order), len(linked), len(inline), db),
    ]
    for position, pid in enumerate(order, start=1):
        body = None if pid in linked else bodies[entries[pid]["hash"]]
        lines += _protocol(position, pid, entries[pid], display.get(pid, {}), body)
    return "\n".join(lines).strip() + "\n"


def export_markdown(
    lock: dict,
    path: str,
    db: str | None = None,
    order: Sequence[str] | None = None,
    metadata: dict[str, str] | None = None,
) -> str:
    """Render and write. Returns what was written."""
    document = to_markdown(lock, db=db, order=order, metadata=metadata)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(document)
    return document
