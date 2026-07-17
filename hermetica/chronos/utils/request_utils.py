# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import requests
import hashlib
import json

def get_protocol_list(
    base_url, headers, user_access: str = "shared_with_user", search_key: str = " ",
    page_size : int = 10, max_pull:int = 20
) -> dict:
    """Get a list of protocols from the protocols.io API."""
    request_url = f"{base_url}/v3/protocols"

    all_protocols = []
    current_page = 1

    params = {
        "filter" : user_access,
        "key" : search_key,
        "order_field" : "date",
        "peer_reviewed" : 0,
        "page_size" : page_size,
        "page_id" : current_page
    }
    while True:
        print(f"Processing Page: {current_page}")
        protocol_list = requests.get(url=request_url, headers=headers, params=params)
        protocol_list.raise_for_status()
        local_list = protocol_list.json()
        protocols = local_list.get("items",[])
        if not protocols:
            break
        all_protocols.extend(protocols)
        if len(protocols) < page_size:
            break
        current_page += 1
        params["page_id"] = current_page
        if current_page == max_pull:
            break
    return all_protocols

def process_protocols(protocols: list) -> dict:
    stripped = [strip_protocol(p, None) for p in protocols]
    blobbed = [blob_protocol(p) for p in stripped]
    return get_unique_protocols(stripped, blobbed)

def strip_protocol(protocol:dict,exclude_fields:list|None):
    # stripping fields that are not relevant to internal versioning
    # For example, it doesn't matter for the core if the protocol 
    # has been published or not (most are not) or peer-reviewed.
    # Stats is essentially "protocol traffic" metrices which are also 
    # kind of useless
    if not exclude_fields:
        exclude_fields = ["stats","published_on","public","peer_reviewed"]
    protocol = {k:v for k,v in protocol.items() if k not in exclude_fields}
    return 0


# blob it after striping so we don't hash variable fields like number of views
def blob_protocol(protocol:dict):
    blob = json.dumps(protocol, sort_keys = True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    blob = hashlib.sha256(blob).hexdigest()
    return blob


def get_unique_protocols(stripped_protocols: list, blobbed_protocols: list) -> dict:
    unique = {}
    for p, h in zip(stripped_protocols, blobbed_protocols):
        if h not in unique:
            unique[h] = p
    return unique
