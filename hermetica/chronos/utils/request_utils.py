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

def blob_protocol(protocol:dict):
    blob = json.dumps(protocol, sort_keys = True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    blob = hashlib.sha256(blob).hexdigest()
    return blob


def strip_protocols(protocols):
    return 0