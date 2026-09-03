# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import re
import uuid

from yaml import safe_dump, safe_load

from compose.compose import ProtocolPipeline
from utils.dates import to_epoch

# A minted file already carries its guids; re-minting one would mint them again
# and orphan every pipeline already written under the old guid.
MINTED = re.compile(r"_minted\.(?:yaml|yml)$")


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
class UnmintedTemplateError(ValueError):
    """A template was read for pipelines before its guids were minted."""


def pipelines_from_template(
    template_path: str, mint: bool = False
) -> list[ProtocolPipeline]:
    """Read a template file into pipelines.

    Reads only. `mint=True` writes the minted twin as well — the write is a
    parameter rather than a surprise, because minting drops a file beside the
    template and changes what identity every pipeline in it will have.
    """
    if mint and not MINTED.search(template_path):
        template, _ = mint_template(template_path)
    else:
        template = read_template(template_path)

    unminted = sorted(
        name
        for name, pipeline in template["pipelines"].items()
        if not pipeline.get("pipeline_guid")
    )
    if unminted:
        raise UnmintedTemplateError(
            f"{template_path} has no guid for: {', '.join(unminted)} — "
            f"run mint_template first, or pass mint=True"
        )

    created_on = to_epoch(template["created_on"])
    creator = template.get("creator")
    return [
        ProtocolPipeline(
            guid=pipeline["pipeline_guid"],
            # The block's own key is the only name a template carries.
            title=name,
            manifest_hash=pipeline.get("manifest_hash"),
            root=pipeline.get("root"),
            executor=pipeline.get("executor"),
            DAG=pipeline.get("protocol_dag") or {},
            created_on=created_on,
            creator=creator,
        )
        for name, pipeline in template["pipelines"].items()
    ]
