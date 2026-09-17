# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import re
import uuid
from dataclasses import replace

from yaml import safe_dump, safe_load

from compose.compose import (
    PipelineArtefact,
    UnsealedProtocolError,
    normalize_dag,
    validate_dag,
)
from compose.store import format_pipeline_entry, write_pipeline
from seal.store import latest_protocols
from utils.constants import PROTOCOL_UID
from utils.dates import to_epoch

# -----------------------------------------------------------------------------#
# Error handling
# -----------------------------------------------------------------------------#


# -----------------------------------------------------------------------------#
# TEMPLATES
# -----------------------------------------------------------------------------#
def read_template(template_path: str) -> dict:
    with open(template_path, "r", encoding="utf-8") as file:
        return safe_load(file)


def mint_template(template_path: str) -> tuple[dict, str]:
    """Give every pipeline a guid if it has none, and write the minted twin.

    The guid is the pipeline's identity across versions, so it is minted once
    and read back forever after. Returns the template and where it was written.
    """
    template = read_template(template_path)
    for pipeline in template["pipelines"].values():
        if not pipeline.get("pipeline_guid"):
            pipeline["pipeline_guid"] = uuid.uuid4().hex

    minted_path = re.sub(r"\.(?:yaml|yml)$", "_minted.yaml", template_path)
    with open(minted_path, "w", encoding="utf-8") as file:
        safe_dump(template, file)
    return template, minted_path


# -----------------------------------------------------------------------------#
# BUILD
# -----------------------------------------------------------------------------#


def pipelines_from_template(
    template_path: str, mint: bool = False
) -> list[PipelineArtefact]:

    if mint and not re.compile(r"_minted\.(?:yaml|yml)$").search(template_path):
        template, _ = mint_template(template_path)
    else:
        template = read_template(template_path)

    unminted = sorted(
        name
        for name, pipeline in template["pipelines"].items()
        if not pipeline.get("pipeline_guid")
    )
    if unminted:
        raise ValueError(
            f"{template_path} has no guid for: {', '.join(unminted)} — "
            f"run mint_template first, or pass mint=True"
        )

    # `protocol_dag` keyed protocols; `dag` keys nodes. A template still on the
    # old key would parse and hydrate a graph that means something else, so the
    # keys are required rather than defaulted.
    malformed = sorted(
        name
        for name, pipeline in template["pipelines"].items()
        if not {"dag", "nodes"} <= set(pipeline)
    )
    if malformed:
        raise ValueError(
            f"{template_path} is missing `dag` or `nodes` for: "
            f"{', '.join(malformed)} — `protocol_dag` was replaced by the two"
        )

    created_on = to_epoch(template["created_on"])
    creator = template.get("creator")
    return [
        build_pipeline(name, spec, created_on, creator)
        for name, spec in template["pipelines"].items()
    ]


def build_pipeline(
    title: str, spec: dict, created_on: int, creator: dict | str | None = None
) -> PipelineArtefact:
    """One pipeline from its written form: `pipeline_guid`, `dag`, `nodes`.

    The one place a written pipeline becomes an artefact — templates and the API
    both come through here, so they normalize and validate identically.
    """
    pipeline = PipelineArtefact(
        guid=spec["pipeline_guid"],
        title=title,
        manifest_hash=None,
        root=spec.get("root"),
        DAG=normalize_dag(spec["dag"] or {}),
        nodes={
            str(node): str(protocol) for node, protocol in (spec["nodes"] or {}).items()
        },
        node_hashes={},
        created_on=created_on,
        creator=creator,
    )
    validate_dag(pipeline.DAG, pipeline.nodes)
    return pipeline


# -----------------------------------------------------------------------------#
# BOOTSTRAP
# -----------------------------------------------------------------------------#
def guids_for_nodes(pipeline: PipelineArtefact, protocol_db: str) -> PipelineArtefact:
    """A hand-written template may name a protocol by `protocol_uid` or by guid;
    the store holds guids only. A bare `protocol_id` is refused, not guessed —
    it collides across sources, which is what the uid exists to prevent.
    """
    by_uid = latest_protocols(
        protocol_db, [n for n in pipeline.nodes.values() if ":" in n], by=PROTOCOL_UID
    )
    by_guid = latest_protocols(
        protocol_db, [n for n in pipeline.nodes.values() if ":" not in n]
    )
    guids, unsealed = {}, {}
    for node, name in pipeline.nodes.items():
        found = by_uid.get(name) if ":" in name else by_guid.get(name)
        if found is None:
            unsealed[node] = name
        else:
            guids[node] = found["protocol_guid"]
    if unsealed:
        raise UnsealedProtocolError(unsealed)
    return replace(pipeline, nodes=guids)


def load_template(
    template_path: str,
    db: str,
    protocol_db: str,
    loaded_at: int | None = None,
    mint: bool = False,
) -> dict[str, list[str]]:
    """Bootstrap the template store from a hand-written file. Returns the diff.

    Every pipeline is resolved to guids before anything is written, so one bad
    name stops the whole file rather than loading half of it.
    """
    pipelines = [
        guids_for_nodes(pipeline, protocol_db)
        for pipeline in pipelines_from_template(template_path, mint=mint)
    ]
    return write_pipeline(db, format_pipeline_entry(pipelines, loaded_at), loaded_at)
