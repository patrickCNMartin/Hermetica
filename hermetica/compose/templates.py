# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import re
import uuid

from yaml import safe_dump, safe_load

from compose.compose import PipelineArtefact, normalize_dag, validate_dag
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
    built = [
        PipelineArtefact(
            guid=pipeline["pipeline_guid"],
            title=name,
            manifest_hash=pipeline.get("manifest_hash"),
            root=pipeline.get("root"),
            DAG=normalize_dag(pipeline["dag"] or {}),
            nodes={
                str(node): str(protocol)
                for node, protocol in (pipeline["nodes"] or {}).items()
            },
            node_hashes=pipeline.get("node_hashes") or {},
            created_on=created_on,
            creator=creator,
        )
        for name, pipeline in template["pipelines"].items()
    ]
    for pipeline in built:
        validate_dag(pipeline.DAG, pipeline.nodes)
    return built
