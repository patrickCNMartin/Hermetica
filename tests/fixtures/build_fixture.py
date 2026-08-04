# -----------------------------------------------------------------------------#
# BUILD THE SYNTHETIC BY-ID FIXTURE
# -----------------------------------------------------------------------------#
"""Transcribe real protocols.io by-ID records into synthetic ones.

Structural transcription, not redaction: every shape is kept — key sets, nesting,
types, null-vs-absent, list lengths, the double-encoded rich-text envelope and its
`\\u0026` separator — and every value is replaced. Nothing real survives.

Seeded, so regenerating produces a byte-identical file; the frozen hash vectors in
the suite depend on that. The input dump is untracked, so a fresh clone cannot
rerun this — the committed JSON is the source of truth. Run from the repo root:

    nix develop --command uv run python tests/fixtures/build_fixture.py
"""

import json
import random
import re
from pathlib import Path

from seal.contract import get_unit_map

SOURCE = Path("db/chronos_protocols.json")
TARGET = Path("tests/fixtures/protocols_by_id.json")
SEED = 20260803

# Chosen for structure, not content. The archetype name is what tests ask for.
ARCHETYPES: dict[str, int] = {
    "empty_versions_null_steps": 115010,  # both version-sourced fallbacks fire
    "signed_urls": 114262,  # & in rich text, bare & in documents
    "baseline": 318392,  # one version, one step
    "empty_versions_with_steps": 91505,  # doi/version_code fall back, steps hash
    "reserved_doi": 230286,  # doi must fall back to "", not to this
    "version_class_differs": 113752,  # the only record where it != id
    "dotted_steps": 233447,  # 54 steps, sub-numbering, 10 before 2
}

# Epoch shift: synthetic timestamps that keep every ordering relation intact, so
# last_modified_on >= created_on still holds and max(modified_on) still picks.
EPOCH_SHIFT = -31_536_000

# Steps kept, in execution order. Each carries a full Draft.js envelope, so the
# long records are mostly repetition of a shape already covered. dotted_steps
# keeps enough to reach "10" and its sub-steps — that is the ordering case.
STEP_CAP: dict[str, int] = {
    "dotted_steps": 14,
    "version_class_differs": 6,
    "reserved_doi": 6,
    "empty_versions_with_steps": 6,
}

# Rich-text blocks kept per document, and elements kept in the long upstream lists
# that no part of the contract reads. They stay present — a fixture that dropped
# them could not show that unasked-for fields are ignored — just not at length.
BLOCK_CAP = 4
# `units` is capped after transcription instead — see cap_units.
LIST_CAP: dict[str, int] = {
    "documents": 2,
    "translations": 1,
    "troubleshooting_items": 1,
    "funders": 1,
    "badges": 1,
    "affiliations": 1,
    "cases": 1,
}


def _step_order(step: dict) -> tuple[int, ...]:
    return tuple(int(part) for part in step["number"].split("."))


def cap_steps(record: dict, keep: int | None) -> dict:
    """Keep the first `keep` steps in execution order, leaving shape untouched."""
    steps = record.get("steps")
    if keep is None or not steps:
        return record
    return record | {"steps": sorted(steps, key=_step_order)[:keep]}


# Entries kept that nothing cites — the fixture must still show that the unit map
# ignores the unused bulk of the catalog.
UNIT_EXTRAS = 2


def cap_units(record: dict) -> dict:
    """Keep the catalog entries this record's entities cite, plus a few spare.

    Runs after transcription, on the capped blocks, so the kept set matches the
    entities that actually survive into the fixture.
    """
    units = record.get("units") or []
    cited = {int(uid) for uid in get_unit_map(record)}
    unused = [unit for unit in units if unit["id"] not in cited]
    return record | {
        "units": [unit for unit in units if unit["id"] in cited] + unused[:UNIT_EXTRAS]
    }


# -----------------------------------------------------------------------------#
# SYNTHETIC VALUE POOLS
# -----------------------------------------------------------------------------#
# Unicode is deliberate: the NFC normalization tests need composed characters to
# work on, and `®`/`µ`/`≥` are the shapes the real corpus actually carries.
WORDS = (
    "buffer",
    "aliquot",
    "incubate",
    "centrifuge",
    "supernatant",
    "pellet",
    "vortex",
    "reagent",
    "sample",
    "gradient",
    "eluate",
    "cartridge",
    "plate",
    "resuspend",
    "digest",
    "peptide",
    "lysate",
    "column",
    "wash",
    "elution",
    "café",
    "≥5 µL",
    "Bench Mixer ®",
    "20 °C",
    "filtrate",
    "overnight",
)
FIRST_NAMES = ("John", "Jane", "Ada", "Rodney", "Mira", "Otto", "Ines", "Pablo")
LAST_NAMES = ("Doe", "Roe", "Sample", "Personman", "Example", "Testcase")
AFFILIATIONS = (
    "Institute of Examples",
    "Synthetic Research Centre",
    "Department of Placeholders",
)
DOMAIN = "provider.com"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_TEXT_RUN = re.compile(r'(\\?"text\\?":\\?")((?:[^"\\]|\\.)*?)(\\?")')
# A URL embedded in rich text has its separators escaped as the literal six
# characters &, so the pattern has to keep walking through them.
_URL = re.compile(r"https?://(?:[^\s\"'<>\\]|\\u0026)+")
_SEPARATOR = re.compile(r"(\\u0026|&)")


# -----------------------------------------------------------------------------#
# DETERMINISTIC MAPPING
# -----------------------------------------------------------------------------#
# One memo per namespace so a value appearing twice maps to the same synthetic
# value — that is what keeps steps[].protocol_id pointing at its own protocol.
class Synth:
    """Seeded, memoized value factory."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.memo: dict[tuple[str, object], object] = {}

    def _get(self, kind: str, key: object, make) -> object:
        slot = (kind, key)
        if slot not in self.memo:
            self.memo[slot] = make()
        return self.memo[slot]

    def ident(self, value: int) -> int:
        return self._get("id", value, lambda: self.rng.randrange(100_000, 999_999))

    def guid(self, value: str) -> str:
        return self._get(
            "guid",
            value,
            lambda: "".join(self.rng.choices("0123456789ABCDEF", k=len(value))),
        )

    def person(self, value: str) -> str:
        return self._get(
            "person",
            value,
            lambda: f"{self.rng.choice(FIRST_NAMES)} {self.rng.choice(LAST_NAMES)}",
        )

    def username(self, value: str) -> str:
        return self._get(
            "user",
            value,
            lambda: (
                f"{self.rng.choice(FIRST_NAMES)}.{self.rng.choice(LAST_NAMES)}".lower()
            ),
        )

    def affiliation(self, value: str) -> str:
        return self._get("aff", value, lambda: self.rng.choice(AFFILIATIONS))

    def email(self, value: str) -> str:
        return self._get(
            "email",
            value,
            lambda: (
                f"{self.rng.choice(FIRST_NAMES)}.{self.rng.choice(LAST_NAMES)}".lower()
                + f"@{DOMAIN}"
            ),
        )

    def slug(self, value: str) -> str:
        return self._get(
            "slug",
            value,
            lambda: "".join(
                self.rng.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=len(value))
            ),
        )

    def host(self, value: str) -> str:
        return self._get("host", value, lambda: f"{self.slug('abcdefg')}.example.org")

    def hexish(self, value: str) -> str:
        return self._get(
            "hex",
            value,
            lambda: "".join(self.rng.choices("0123456789abcdef", k=len(value))),
        )

    def prose(self, words: int = 8) -> str:
        return " ".join(self.rng.choices(WORDS, k=words)).capitalize() + "."

    def title(self) -> str:
        return f"{self.prose(3).rstrip('.')} protocol"


# -----------------------------------------------------------------------------#
# STRING TRANSCRIPTION
# -----------------------------------------------------------------------------#
def synth_url(url: str, s: Synth) -> str:
    """Rebuild a URL synthetically, keeping its query structure and separators.

    Param *names* and the separator form survive untouched — the signed-URL scrub
    matches on those, so replacing them would hollow out the test that needs this
    fixture. Only hosts, path segments and param values are synthesized.
    """
    head, mark, query = url.partition("?")
    scheme, _, rest = head.partition("://")
    host, _, path = rest.partition("/")

    segments = []
    for segment in path.split("/"):
        if not segment:
            continue
        stem, dot, extension = segment.rpartition(".")
        segments.append(f"{s.slug(stem)}{dot}{extension}" if dot else s.slug(segment))
    tail = "/" + "/".join(segments) if segments else ""
    rebuilt = f"{scheme}://{s.host(host)}{tail}"
    if not mark:
        return rebuilt

    parts = []
    for part in _SEPARATOR.split(query):
        if _SEPARATOR.fullmatch(part):
            parts.append(part)
            continue
        name, equals, value = part.partition("=")
        parts.append(name + equals + (s.hexish(value) if value else ""))
    return f"{rebuilt}?{''.join(parts)}"


def scrub_string(value: str, s: Synth) -> str:
    """Replace anything identifying that can hide inside an arbitrary string."""
    value = _EMAIL.sub(lambda m: s.email(m.group(0)), value)
    return _URL.sub(lambda m: synth_url(m.group(0), s), value)


def _reserialize(document) -> str:
    """Serialize the way protocols.io does, so the envelope stays byte-faithful.

    Upstream is PHP: compact separators, non-ASCII escaped, and `&` escaped to the
    literal six characters \\u0026. That escape is the separator the signed-URL
    scrub matches at depth, so reproducing it is not cosmetic.
    """
    return json.dumps(
        document, separators=(",", ":"), ensure_ascii=True, sort_keys=False
    ).replace("&", "\\u0026")


def rich_text(value: str, s: Synth, blocks: int = BLOCK_CAP) -> str:
    """Replace the prose inside a Draft.js document, keeping its structure.

    Falls back to substituting text runs in place when the field is not a Draft.js
    document (some are plain HTML), so no field is left un-transcribed.
    """
    if not value:
        return value
    try:
        document = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return scrub_string(
            _TEXT_RUN.sub(
                lambda m: (
                    m.group(1) + (s.prose(6) if m.group(2).strip() else "") + m.group(3)
                ),
                value,
            ),
            s,
        )

    if not isinstance(document, dict) or "blocks" not in document:
        return _reserialize(transcribe(document, s))

    kept = document["blocks"][:blocks]
    for block in kept:
        block["entityRanges"] = block.get("entityRanges", [])[:2]
    document["blocks"] = kept

    # entityMap is keyed by the ranges the surviving blocks still point at.
    referenced = {str(r["key"]) for b in kept for r in b.get("entityRanges", [])}
    document["entityMap"] = {
        k: v for k, v in (document.get("entityMap") or {}).items() if k in referenced
    }
    # Transcribe the whole document, not just block text: a block's `data` can
    # carry another serialized document, and that nests arbitrarily.
    return _reserialize(transcribe(document, s))


# -----------------------------------------------------------------------------#
# STRUCTURAL WALK
# -----------------------------------------------------------------------------#
IDENTITY_KEYS = {
    "id",
    "protocol_id",
    "version_class",
    "fork_id",
    "original_id",
    "previous_id",
    "item_id",
    "space_id",
    "version_id",
}
GUID_KEYS = {"guid", "previous_guid", "original_guid"}
# Values kept verbatim because they are enums or presentation, not content.
# Everything NOT listed here is synthesized — an allowlist, for the same reason
# HASH_FIELDS is one: a denylist silently admits the field nobody thought of.
STRUCTURAL_KEYS = {
    "mime",
    "number",
    "status",
    "key",
    "align",
    "textAlignment",
    "direction",
    "style",
    "mutability",
}
RICH_KEYS = {
    "description",
    "warning",
    "disclaimer",
    "guidelines",
    "materials_text",
    "before_start",
    "protocol_references",
    "step",
    "acknowledgements",
    "ethics_statement",
    "manuscript_citation",
}
EPOCH_KEYS = {"created_on", "modified_on", "published_on", "changed_on"}
PERSON_KEYS = {"name", "full_name"}
NAME_PART_KEYS = {"first_name", "last_name"}


def transcribe(value, s: Synth, key: str | None = None):
    """Return a synthetic value of the same shape as `value`."""
    # The unit catalog is structural: entity `unit` ids are kept verbatim, so the
    # names they resolve to must be too, and SI symbols identify nobody.
    if key == "units":
        return value
    if isinstance(value, dict):
        return {k: transcribe(v, s, k) for k, v in value.items()}
    if isinstance(value, list):
        return [transcribe(v, s, key) for v in value[: LIST_CAP.get(key, len(value))]]

    # bool before int: bool subclasses int and must not be renumbered.
    if isinstance(value, bool) or value is None:
        return value

    if isinstance(value, int):
        if key in IDENTITY_KEYS and value:
            return s.ident(value)
        if key in EPOCH_KEYS and value:
            return value + EPOCH_SHIFT
        return value

    if not isinstance(value, str):
        return value

    if not value:
        return value
    if key in STRUCTURAL_KEYS:  # includes `number` — execution order, verbatim
        return value
    # Rich text nests: a block's `data` can hold another serialized document.
    if value.lstrip().startswith('{"blocks"') or '\\"blocks\\"' in value[:24]:
        return rich_text(value, s)
    if key in GUID_KEYS:
        return s.guid(value)
    if key in RICH_KEYS:
        return rich_text(value, s)
    if key in PERSON_KEYS:
        return s.person(value)
    if key in NAME_PART_KEYS:
        return s.person(value).split()[0 if key == "first_name" else 1]
    if key == "username":
        return s.username(value)
    if key in {"affiliation", "affiliations"}:
        return s.affiliation(value)
    if key == "section":
        return f"<p>{s.prose(3)}</p>"
    if key == "keywords":
        return ",".join(s.prose(1).rstrip(".") for _ in range(3))
    # `type` is an enum almost everywhere ("unstyled", "LINK"), but protocols.io
    # also uses it for free text like a product category. A space means content.
    if key == "type":
        return s.prose(2) if " " in value else value
    if key in {"color", "section_color"}:
        return "#" + s.hexish(value.lstrip("#"))
    if key in {"title", "title_html"}:
        title = s.title()
        return f"<p>{title}</p>" if key == "title_html" else title
    if key in {"uri", "version_uri", "version_code"}:
        return s.slug(value) if len(value) > 4 else value
    if key in {"ofn", "filename", "file_name"}:
        return f"{s.slug('abcdefgh')}.pdf"
    if key in {
        "url",
        "link",
        "source",
        "placeholder",
        "thumb_url",
        "affiliation_url",
        "image_url",
    }:
        return scrub_string(value, s)
    if key in {"doi", "reserved_doi"}:
        return f"10.99999/example.org.{s.slug('abcdefghijkl')}/v1"
    if key == "text":
        return s.prose(6)

    # Unknown key: synthesize. Anything genuinely structural belongs above.
    return s.prose(3)


# -----------------------------------------------------------------------------#
# ENTRY
# -----------------------------------------------------------------------------#
def build() -> dict[str, dict]:
    """Map every archetype to its transcribed record."""
    records = {p["id"]: p for p in json.loads(SOURCE.read_text(encoding="utf-8"))}
    s = Synth(SEED)
    # Sorted so the seeded stream is consumed in a fixed order.
    return {
        name: cap_units(
            transcribe(cap_steps(records[ARCHETYPES[name]], STEP_CAP.get(name)), s)
        )
        for name in sorted(ARCHETYPES)
    }


def main() -> None:
    fixture = build()
    TARGET.write_text(
        json.dumps(fixture, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    size = TARGET.stat().st_size
    print(f"{TARGET}: {len(fixture)} records, {size // 1024} kB")
    for name, record in sorted(fixture.items()):
        steps = record.get("steps")
        print(
            f"  {name:26} id={record['id']:<8} "
            f"versions={len(record.get('versions') or [])} "
            f"steps={'null' if steps is None else len(steps)}"
        )


if __name__ == "__main__":
    main()
