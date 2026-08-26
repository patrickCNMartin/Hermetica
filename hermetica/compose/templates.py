# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import re
import uuid

from yaml import safe_dump, safe_load

from compose.compose import ProtocolPipeline


# -----------------------------------------------------------------------------#
# TEMPLATES
# -----------------------------------------------------------------------------#
def read_template(template_path: str):
    with open(template_path, "r") as file:
        template = safe_load(file)
    return template


def mint_template(template_path: str):
    template = read_template(template_path)
    pipelines = template["pipelines"]
    minted_template = {}
    for k, v in pipelines:
        if not v["pipeline_guid"]:
            v["pipeline_guid"] = uuid.uuid4().hex()
        minted_template[k] = v
    template["pipelines"] = minted_template
    template_path = re.sub(r"\.(?:yaml|yml)$", "_minted.yaml", template_path)
    with open(template_path, "w") as f:
        safe_dump(minted_template, f)
    return template


# Build
def pp_from_template(template_path: str) -> ProtocolPipeline:
    if re.find("minted", template_path):
        template = read_template(template_path)
    else:
        template = mint_template(template_path)

    pipelines = template["pipelines"]
    pipeline_list = []
    for p in pipelines:
        tmp = ProtocolPipeline(
            guid=p["guid"],
            tite=p["guid"],
            manifest_hash=p["manifest_hash"],
            root=p["root"],
            executor=p["executor"],
            DAG=p["protocol_dag"],
            created_on=template["created_on"],
            creator=template["creator"],
        )
        pipeline_list.append(tmp)
    return pipeline_list
