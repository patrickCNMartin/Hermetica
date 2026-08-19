# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import re
from typing import Any

from seal.contract import ProtocolArtefact, parse_rich_text

# -----------------------------------------------------------------------------#
# PROTOCOLS.IO RESPONSE SHAPE
# -----------------------------------------------------------------------------#
# Rich-text fields arrive as a JSON *string* holding a Draft.js envelope, and the
# quantities live in `entityMap`, not in the block text — the text carries only a
# placeholder character where each one belongs.
# Every hashed rich-text field belongs here: the unit map is built from this
# list, so a field hashed but not scanned stores text citing unit ids the map
# cannot resolve, and the catalog is gone by then.
RICH_TEXT_FIELDS: tuple[str, ...] = (
    "description",
    "guidelines",
    "before_start",
    "materials_text",
    "disclaimer",
    "warning",
    "protocol_references",
)

UNIT_KEYS: tuple[str, ...] = ("unit", "temperatureUnit")


# -----------------------------------------------------------------------------#
# SIGNED URL SCRUBBING
# -----------------------------------------------------------------------------#
# Rich-text fields (materials_text, guidelines, ...) embed attachments as URLs
# carrying AWS/CloudFront signing params that are regenerated on every request.
# They are credentials, not protocol content — left in, the hash changes nightly
# for any protocol with an attached file.
VOLATILE_URL_PARAMS: tuple[str, ...] = (
    "X-Amz-Security-Token",
    "X-Amz-SignedHeaders",
    "X-Amz-Credential",
    "X-Amz-Algorithm",
    "X-Amz-Signature",
    "X-Amz-Expires",
    "X-Amz-Date",
    "Key-Pair-Id",
    "Signature",
    "Policy",
    "Expires",
)

# The separator inside an embedded document is the literal text `&`, not a
# decoded `&` — hence the backslash in the value's excluded set. Requiring a
# leading separator keeps prose like "Expires=" in a protocol from being hit.
_SIGNED_PARAM = re.compile(
    r"([?&]|\\u0026)("
    + "|".join(re.escape(p) for p in VOLATILE_URL_PARAMS)
    + r")=[^&\"'\s\\]*",
    re.IGNORECASE,
)


def scrub_signed_urls(value):
    """Blank the value of every signing parameter, leaving structure intact."""
    if isinstance(value, str):
        return _SIGNED_PARAM.sub(r"\1\2=", value)
    if isinstance(value, dict):
        return {k: scrub_signed_urls(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_signed_urls(v) for v in value]
    return value


# -----------------------------------------------------------------------------#
# DATA PREPARATION
# -----------------------------------------------------------------------------#
def get_steps(protocol: dict) -> dict:
    # `or []` not a default: upstream sends steps=null, not a missing key.
    steps = protocol.get("steps") or []
    # trim the step response to only include fields we really need.
    # Content only — ordering lives in the chain.
    step_fields = ["id", "guid", "section", "step", "critical"]
    steps_trimmed = [{k: v for k, v in st.items() if k in step_fields} for st in steps]
    return steps_trimmed


def _step_order(step: dict) -> tuple[int, ...]:
    # `number` is a dotted string ("7.1"). Sorting it as text puts 10 before 2.
    return tuple(int(part) for part in step["number"].split("."))


def get_step_chain(steps: list[dict]) -> list:
    """Step ids in execution order. Takes the raw steps — `number` is trimmed."""
    return [st["id"] for st in sorted(steps, key=_step_order)]


def _cite_units(node: Any, cited: set[int]) -> None:
    """Collect unit ids from every entity, descending into nested documents."""
    if isinstance(node, dict):
        data = node.get("data")
        if isinstance(data, dict):
            cited.update(
                data[key]
                for key in UNIT_KEYS
                # bool subclasses int; an entity never carries one, but the
                # to_epoch precedent says exclude it rather than rely on that.
                if isinstance(data.get(key), int) and not isinstance(data[key], bool)
            )
        for value in node.values():
            _cite_units(value, cited)
    elif isinstance(node, list):
        for value in node:
            _cite_units(value, cited)


def get_unit_map(protocol: dict) -> dict[str, str]:
    """Unit id -> name, restricted to the units this protocol's rich text cites.

    The upstream `units` list is a shared catalog — ~45 unused entries per
    protocol, plus viewer-permission fields — so hashing it whole would re-fork
    every protocol whenever protocols.io edits the catalog. Ids the catalog
    cannot resolve are omitted and surface as a marker at render time.
    """
    cited: set[int] = set()
    for field in RICH_TEXT_FIELDS:
        _cite_units(parse_rich_text(protocol.get(field)), cited)
    for step in protocol.get("steps") or []:
        _cite_units(parse_rich_text(step.get("step")), cited)

    catalog = {unit["id"]: unit["name"] for unit in protocol.get("units") or []}
    return {str(uid): catalog[uid] for uid in sorted(cited) if uid in catalog}


def build_protocol_artefact(protocol: dict) -> ProtocolArtefact:
    # Scrubbed once, up front: the artefact is frozen, so nothing can be
    # rewritten after construction.
    protocol = scrub_signed_urls(protocol)
    steps = get_steps(protocol)
    chain = get_step_chain(protocol.get("steps") or [])

    return ProtocolArtefact(
        id=protocol["id"],
        guid=protocol["guid"],
        title=protocol["title"],
        description=protocol["description"],
        guidelines=protocol["guidelines"],
        before_start=protocol["before_start"],
        disclaimer=protocol["disclaimer"],
        warning=protocol["warning"],
        materials=protocol["materials_text"],
        steps=steps,
        chain=chain,
        units=get_unit_map(protocol),
        uri=protocol["uri"],
        doi=protocol.get("doi") or "",
        reserved_doi=protocol["reserved_doi"],
        version_class=protocol["version_class"],
        protocol_references=protocol["protocol_references"],
        created_on=protocol["created_on"],
        keywords=protocol["keywords"],
        authors=protocol["authors"],
        creator=protocol["creator"],
    )
