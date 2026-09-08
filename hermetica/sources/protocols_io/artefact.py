# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from typing import Any

from seal.contract import ProtocolArtefact, parse_rich_text
from sources.protocols_io.config import (
    RICH_TEXT_FIELDS,
    SIGNED_PARAM,
    SOURCE_NAME,
    UNIT_KEYS,
)


# -----------------------------------------------------------------------------#
# DATA PREPARATION
# -----------------------------------------------------------------------------#
def scrub_signed_urls(value):
    """Blank the value of every signing parameter, leaving structure intact."""
    if isinstance(value, str):
        return SIGNED_PARAM.sub(r"\1\2=", value)
    if isinstance(value, dict):
        return {k: scrub_signed_urls(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_signed_urls(v) for v in value]
    return value


def get_steps(protocol: dict) -> dict:
    # `or []` not a default: upstream sends steps=null, not a missing key.
    steps = protocol.get("steps") or []
    # trim the step response to only include fields we really need.
    # Content only — ordering lives in the chain.
    step_fields = ["id", "guid", "section", "step", "critical"]
    steps_trimmed = [{k: v for k, v in st.items() if k in step_fields} for st in steps]
    return steps_trimmed


def step_order(step: dict) -> tuple[int, ...]:
    # `number` is a dotted string ("7.1"). Sorting it as text puts 10 before 2.
    return tuple(int(part) for part in step["number"].split("."))


def get_step_chain(steps: list[dict]) -> list:
    """Step ids in execution order. Takes the raw steps — `number` is trimmed."""
    return [st["id"] for st in sorted(steps, key=step_order)]


def cite_units(node: Any, cited: set[int]) -> None:
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
            cite_units(value, cited)
    elif isinstance(node, list):
        for value in node:
            cite_units(value, cited)


def get_unit_map(protocol: dict) -> dict[str, str]:
    """Unit id -> name, restricted to the units this protocol's rich text cites.

    The upstream `units` list is a shared catalog — ~45 unused entries per
    protocol, plus viewer-permission fields — so hashing it whole would re-fork
    every protocol whenever protocols.io edits the catalog. Ids the catalog
    cannot resolve are omitted and surface as a marker at render time.
    """
    cited: set[int] = set()
    for field in RICH_TEXT_FIELDS:
        cite_units(parse_rich_text(protocol.get(field)), cited)
    for step in protocol.get("steps") or []:
        cite_units(parse_rich_text(step.get("step")), cited)

    catalog = {unit["id"]: unit["name"] for unit in protocol.get("units") or []}
    return {str(uid): catalog[uid] for uid in sorted(cited) if uid in catalog}


def build_protocol_artefact(
    protocol: dict, source: str = SOURCE_NAME
) -> ProtocolArtefact:
    # Scrubbed once, up front: the artefact is frozen, so nothing can be
    # rewritten after construction.
    protocol = scrub_signed_urls(protocol)
    steps = get_steps(protocol)
    chain = get_step_chain(protocol.get("steps") or [])

    return ProtocolArtefact(
        source=source,
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
