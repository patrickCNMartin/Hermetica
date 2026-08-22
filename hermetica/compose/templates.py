# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import uuid
import re
from compose.compose import ProtocolPipeline
from seal.store import connect
from yaml import safe_load, safe_dump
# -----------------------------------------------------------------------------#
# TEMPLATES
# -----------------------------------------------------------------------------#
def read_template(template_path:str):
    with open(template_path,"r") as file:
        template = safe_load(file)
    return template

def mint_template(template_path:str):
    pipelines = read_template(template_path)['pipelines']

    minted_template = {}
    for k,v in pipelines:
        if not v['pipeline_guid']:
            v['pipeline_guid'] = uuid.uuid4().hex()
        minted_template[k] = v
    template_path = re.sub(".yaml|.yml","_minted.yaml", template_path)
    with open(template_path, "w") as f:
        safe_dump(minted_template,f)
    return minted_template

# Build Pr
def pipeline_from_template(template_path:str, db_path:str) -> ProtocolPipeline:
    if re.find("minted",template_path):
        template = read_template(template_path)
    else:
        template = mint_template(template_path)

    return ProtocolPipeline(
        guid=template[]
    )

# check if guid already exists in pipeline data base
def verify_template(template_path:str, db_path:str):
    return 0

