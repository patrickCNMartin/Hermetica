# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any

# -----------------------------------------------------------------------------#
# CONTENT CONTRACT
# -----------------------------------------------------------------------------#
# Specify the fields that are going to hashed and version controlled
# Essentially my whitelisted .gitignroe contract
HASH_FIELDS: tuple[str, ...] = (
    "doi",
    "reserved_doi",
    "id",
    "guid",
    "title",
    "description",
    "disclaimer",
    "warning",
    "materials",
    "steps",
    "chain",
    "units",
    "uri",
    "version_class",
    "version_code",
)
# Specify other in
METADATA_FIELDS: tuple[str, ...] = (
    "created_on",
    "creator",
    "authors",
    "last_modified_on",
)


# Protocols pulled from protocols.io will go through reformatting to create this
# The reason is that we have nested API requests to pull all the
# relevant information so creating a "template" to hold that info is usefull.
PROTOCOL_FIELDS: tuple[str, ...] = HASH_FIELDS + METADATA_FIELDS


# The template itself. Frozen: an artefact is a snapshot of upstream content,
# not a working buffer — mutating one after hashing would desync blob and hash.
@dataclass(frozen=True, slots=True)
class ProtocolArtefact:
    # --- hashed (HASH_FIELDS) ---------------------------------------------- #
    id: int
    guid: str
    title: str
    description: str
    disclaimer: str
    warning: str
    materials: str
    steps: list[dict]
    chain: list[int]
    units: dict[str, str]
    uri: str
    doi: str
    reserved_doi: str
    version_class: int
    version_code: str
    # --- retained, never hashed (METADATA_FIELDS) -------------------------- #
    created_on: int
    last_modified_on: int
    authors: list[dict] | None = None
    creator: dict | None = None

    def to_dict(self) -> dict:
        """Full artefact as a plain dict — the stored/metadata-bearing form."""
        return asdict(self)

    def hashable(self) -> dict:
        """Only the fields HASH_FIELDS declares — the form that gets hashed."""
        return {field: getattr(self, field) for field in HASH_FIELDS}

    def metadata(self) -> dict:
        """Get meta data fields"""
        return {field: getattr(self, field) for field in METADATA_FIELDS}


# Hashing algortihm
HASH_ALGORITHM = "sha256"

# -----------------------------------------------------------------------------#
# SANITY CHECKS - take raw pull and check if fields are present
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


# Rich-text fields arrive as a JSON *string* holding a Draft.js envelope, and the
# quantities live in `entityMap`, not in the block text — the text carries only a
# placeholder character where each one belongs.
RICH_TEXT_FIELDS: tuple[str, ...] = (
    "description",
    "materials_text",
    "disclaimer",
    "warning",
)
UNIT_KEYS: tuple[str, ...] = ("unit", "temperatureUnit")


def parse_rich_text(value: Any) -> dict | None:
    """A Draft.js envelope, or None when the field holds no rich text."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip().startswith("{"):
        return None
    # Anything shaped like an envelope but unparseable is corrupt: silently
    # skipping it would drop units from a hashed field.
    return json.loads(value)


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


def get_version(protocol: dict) -> dict:
    """The versions entry this pull describes; {} when upstream lists none."""
    versions = protocol.get("versions") or []
    if not versions:
        return {}
    return max(versions, key=lambda v: v.get("modified_on") or 0)


def build_protocol_artefact(protocol: dict) -> ProtocolArtefact:
    # Scrubbed once, up front: the artefact is frozen, so nothing can be
    # rewritten after construction.
    protocol = scrub_signed_urls(protocol)
    steps = get_steps(protocol)
    chain = get_step_chain(protocol.get("steps") or [])
    version = get_version(protocol)

    # doi/version_code/modified_on live only in the versions entry, and upstream
    # leaves that list empty for some protocols — no version record means no
    # recorded modification, so creation is the effective last edit.
    return ProtocolArtefact(
        id=protocol["id"],
        guid=protocol["guid"],
        title=protocol["title"],
        description=protocol["description"],
        disclaimer=protocol["disclaimer"],
        warning=protocol["warning"],
        materials=protocol["materials_text"],
        steps=steps,
        chain=chain,
        units=get_unit_map(protocol),
        uri=protocol["uri"],
        doi=version.get("doi") or "",
        reserved_doi=protocol["reserved_doi"],
        version_class=protocol["version_class"],
        version_code=version.get("version_code") or "",
        created_on=protocol["created_on"],
        last_modified_on=version.get("modified_on") or protocol["created_on"],
        authors=protocol["authors"],
        creator=protocol["creator"],
    )


def _normalize(obj: Any) -> Any:
    """Recursively NFC-normalize every string, key or value."""
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, dict):
        return {_normalize(k): _normalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalize(v) for v in obj]
    return obj


def canonical_json(obj: Any) -> bytes:
    """Serialize to deterministic UTF-8 bytes.

    Sorted keys, no whitespace, ASCII-escaped, NFC strings. Rejects NaN/Infinity.
    """
    return json.dumps(
        _normalize(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


# -----------------------------------------------------------------------------#
# HASHING
# -----------------------------------------------------------------------------#
def hash_bytes(blob: bytes) -> str:
    """SHA256 hexdigest of already-canonical bytes."""
    return f"{HASH_ALGORITHM}:{hashlib.sha256(blob).hexdigest()}"


# actual protocol that is going to be stored
def protocol_blob(artefact: ProtocolArtefact) -> bytes:
    """Prepare protocol blob from protocol artefact"""
    protocol = artefact.hashable()
    return canonical_json(protocol)


# Hash fingerprint for thhat protocol
def protocol_hash(
    artefact: ProtocolArtefact,
) -> str:
    """Content hash of a selected protocol, metadata excluded."""
    return hash_bytes(protocol_blob(artefact))
