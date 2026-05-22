# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import requests



def get_protocol_list(base_url,headers,filter:str = "shared_with_user",search_key:str=" ")->dict:
    request_url = f"{base_url}/v3/protocols?filter={filter}&key={search_key}"
    protocol_list = requests.get(url = request_url, headers=headers)
    protocol_list.raise_for_status()
    return protocol_list.json()