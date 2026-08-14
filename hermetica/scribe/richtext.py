# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import re
from typing import Any

from seal.contract import parse_rich_text

# -----------------------------------------------------------------------------#
# ENTITIES
# -----------------------------------------------------------------------------#
# The quantities live here, not in the block text: a block carries a single
# placeholder character where each entity belongs. Dropping entities deletes
# every mass, volume, duration and temperature from the rendered protocol.
DURATION_UNITS: tuple[tuple[str, int], ...] = (
    ("d", 86400),
    ("h", 3600),
    ("min", 60),
    ("s", 1),
)


def unit_name(uid: Any, units: dict[str, str]) -> str:
    """Resolve a unit id, or mark it. An unresolved unit must never look resolved."""
    if uid is None:
        return ""
    return units.get(str(uid)) or f"[unit:{uid}]"


def _measure(value: Any, uid: Any, units: dict[str, str]) -> str:
    return f"{value if value is not None else ''} {unit_name(uid, units)}".strip()


def _duration(seconds: Any) -> str:
    """Durations are stored in seconds and carry no unit id."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return ""
    seconds = int(seconds)
    if seconds <= 0:
        return "0 s"
    parts = []
    for label, size in DURATION_UNITS:
        whole, seconds = divmod(seconds, size)
        if whole:
            parts.append(f"{whole} {label}")
    return " ".join(parts)


def _centrifuge(data: dict, units: dict[str, str]) -> str:
    speed = _measure(data.get("centrifuge"), data.get("unit"), units)
    temp = _measure(data.get("temperature"), data.get("temperatureUnit"), units)
    held = _duration(data.get("duration"))
    return ", ".join(part for part in (speed, temp, held) if part)


def _link(data: dict) -> str:
    url = data.get("url") or ""
    return f"<{url}>" if url else ""


def _catalog(kind: str, name: Any, maker: Any, sku: Any) -> str:
    """`name (maker, sku)`. Entries carry no vendor or sku, so parts drop."""
    name = (name or "").strip()
    detail = ", ".join(
        part for part in ((maker or "").strip(), (sku or "").strip()) if part
    )
    if not name:
        return f"[{kind}]"
    return f"{name} ({detail})" if detail else name


def render_entity(entity: dict, units: dict[str, str]) -> str:
    """One entity as text. Anything without a renderer is marked, never dropped."""
    kind = entity.get("type")
    data = entity.get("data") or {}
    if kind == "amount":
        return _measure(data.get("amount"), data.get("unit"), units)
    if kind == "concentration":
        return _measure(data.get("concentration"), data.get("unit"), units)
    if kind == "temperature":
        return _measure(data.get("temperature"), data.get("unit"), units)
    if kind == "duration":
        return _duration(data.get("duration"))
    if kind == "centrifuge":
        return _centrifuge(data, units)
    if kind == "ph":
        return f"pH {data.get('number', '')}".strip()
    if kind == "reagents":
        return _catalog(
            kind,
            data.get("name"),
            (data.get("vendor") or {}).get("name"),
            data.get("sku"),
        )
    if kind == "equipment":
        # `vendor` is the reseller, `brand` the maker — a Beckman instrument
        # comes back with vendor "Ramcon".
        return _catalog(kind, data.get("name"), data.get("brand"), data.get("sku"))
    if kind == "link":
        return _link(data)
    if kind == "emoji":
        return data.get("name") or ""
    return f"[{kind}]"


# -----------------------------------------------------------------------------#
# BLOCKS
# -----------------------------------------------------------------------------#
_SPACES = re.compile(r"[^\S\n]{2,}")
# Upstream leans on the editor chip for spacing, so the placeholder often sits
# flush against the neighbouring word — "Prepare[ ]of" becomes "10 mLof".
_TIGHT_AFTER = ".,;:!?)]}%"


def render_block(block: dict, entity_map: dict, units: dict[str, str]) -> str:
    """Splice every entity into the block's placeholder positions."""
    text = block.get("text") or ""
    # Descending offset: an earlier splice would shift every later one.
    spans = sorted(
        block.get("entityRanges") or [], key=lambda r: r["offset"], reverse=True
    )
    for span in spans:
        entity = entity_map.get(str(span["key"]))
        rendered = (
            render_entity(entity, units)
            if entity is not None
            else f"[entity:{span['key']}]"
        )
        before, after = text[: span["offset"]], text[span["offset"] + span["length"] :]
        if rendered:
            if before and not before[-1].isspace():
                rendered = " " + rendered
            if after and not after[0].isspace() and after[0] not in _TIGHT_AFTER:
                rendered = rendered + " "
        text = before + rendered + after
    # Placeholders leave runs of blanks behind wherever an entity rendered empty.
    return _SPACES.sub(" ", text).strip()


def render_document(raw: Any, units: dict[str, str] | None = None) -> str:
    """A Draft.js envelope as plain text, one paragraph per block.

    Flat by design for now: no list markers and no inline styling, so the
    quantities are the only thing this has to get right.
    """
    document = parse_rich_text(raw)
    if not isinstance(document, dict) or "blocks" not in document:
        return ""
    units = units or {}
    entity_map = document.get("entityMap") or {}
    blocks = (
        render_block(block, entity_map, units) for block in document["blocks"] or []
    )
    return "\n\n".join(block for block in blocks if block)
