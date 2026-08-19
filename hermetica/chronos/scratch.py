# -----------------------------------------------------------------------------#
# SCRATCH — live probes against protocols.io. Not part of the pull path.
# Run:  nix develop --command uv run python -m chronos.scratch
#
# Answers one question: what does the nightly pull never see, and why?
# Every list call here is BOUNDED. `filter=public` is global (all of
# protocols.io), so it is only ever asked for its total_results, never walked.
# -----------------------------------------------------------------------------#
import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

from sources.protocols_io.client import _call_api

load_dotenv(dotenv_path=Path.cwd() / "env" / ".env")

API_KEY = os.getenv("API_KEY", "")
BASE_URL = os.getenv("BASE_URL", "")
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

# The published, DOI-bearing protocol the nightly pull misses.
TARGET_CODE = "j8nlk9en1v5r"
# Every one of the 61 pulled protocols carries this space_id.
KNOWN_SPACE_ID = 106822
DUMP = Path(os.getenv("DB", "db")) / "chronos_protocols.json"


def banner(text: str) -> None:
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")


def probe(label: str, url: str, params: dict | None = None) -> dict | None:
    """One call. Never raises — a failed probe must not stop the others."""
    try:
        payload = _call_api(url, HEADERS, params).json()
    except Exception as err:  # noqa: BLE001 - a probe reports, it does not handle
        print(f"  [FAIL] {label}: {type(err).__name__} {err}")
        return None
    print(f"  [ ok ] {label}  status_code={payload.get('status_code')}")
    return payload


# -----------------------------------------------------------------------------#
# 1. THE TARGET — what a published protocol actually looks like
# -----------------------------------------------------------------------------#
banner("1. The published protocol, fetched by DOI form")

target = probe(TARGET_CODE, f"{BASE_URL}/v4/protocols/protocols.io.{TARGET_CODE}")
target_id = None
if target:
    body = target.get("payload") or {}
    target_id = body.get("id")
    for key in (
        "id",
        "guid",
        "uri",
        "doi",
        "reserved_doi",
        "is_doi_reserved",
        "public",
        "published_on",
        "created_on",
        "version_class",
        "version_id",
        "version_uri",
        "space_id",
        "item_id",
        "type_id",
        "fork_id",
    ):
        print(f"    {key:18} {body.get(key)!r}")
    print(f"    {'versions':18} {len(body.get('versions') or [])} entries")
    print(f"    {'status':18} {body.get('status')!r}")
    print(f"    {'keywords':18} {(body.get('keywords') or '')[:70]!r}")
    print(f"    {'version_class==id':18} {body.get('version_class') == body.get('id')}")
    print(f"    {'same workspace':18} {body.get('space_id') == KNOWN_SPACE_ID}")

# -----------------------------------------------------------------------------#
# 2. WHO ARE WE, AND WHICH WORKSPACE IS 106822?
# -----------------------------------------------------------------------------#
banner("2. Session identity and workspace uri")

username = None
workspace_uri = None

profile = probe("session/profile", f"{BASE_URL}/v3/session/profile")
if profile:
    user = profile.get("user") or profile.get("payload") or {}
    username = user.get("username")
    print(f"    username  {username!r}   name {user.get('name')!r}")
    # The workspace uri is not documented as living here; dump anything that
    # smells like one rather than guessing the key.
    for key, value in sorted(user.items()):
        if any(s in key for s in ("space", "workspace", "affiliation", "uri")):
            print(f"    {key:22} {json.dumps(value)[:120]}")

# /v3/researchers/<u>/workspaces is documented as *public* workspaces, and this
# one is private — it returned 0 items. Top folders is the other route in.
folders: list[tuple[str, str]] = []
tops = probe(
    "filemanager/folders?top", f"{BASE_URL}/v3/filemanager/folders", {"top": 1}
)
for folder in (tops or {}).get("folders") or []:
    guid = folder.get("guid")
    name = (
        folder.get("name") or folder.get("title") or folder.get("icon", {}).get("code")
    )
    folders.append((guid, str(name)))
    print(f"    {str(name)[:34]:36} {guid}")

# -----------------------------------------------------------------------------#
# 3. FILTER CENSUS — how big is each slice? ONE page each, page_size=1.
# -----------------------------------------------------------------------------#
banner("3. What each list filter can even see (total_results only)")
print("  NOTE: `public` is ALL of protocols.io, not this workspace. Never walked.\n")

for filt in ("shared_with_user", "user_public", "user_private", "public"):
    payload = probe(
        filt,
        f"{BASE_URL}/v3/protocols",
        {
            "filter": filt,
            "key": " ",
            "order_field": "id",
            "fields": "id",
            "page_size": 1,
            "page_id": 0,
        },
    )
    if payload:
        total = (payload.get("pagination") or {}).get("total_results")
        print(f"    total_results = {total}")

# -----------------------------------------------------------------------------#
# 4. DOES ANY BOUNDED FILTER CONTAIN THE TARGET?
# -----------------------------------------------------------------------------#
banner("4. Walking the workspace-sized filters, looking for the target id")

seen: dict[str, set] = {}
for filt in ("shared_with_user", "user_public", "user_private"):
    ids: set = set()
    for page in range(0, 30):  # hard cap: 30 pages of 100 = 3000, plenty
        payload = probe(
            f"{filt} p{page}",
            f"{BASE_URL}/v3/protocols",
            {
                "filter": filt,
                "key": " ",
                "order_field": "id",
                "fields": "id",
                "page_size": 100,
                "page_id": page,
            },
        )
        if not payload:
            break
        batch = payload.get("items") or []
        ids.update(item["id"] for item in batch)
        if not (payload.get("pagination") or {}).get("next_page"):
            break
    seen[filt] = ids
    hit = "YES" if target_id in ids else "no"
    print(f"    {filt:18} {len(ids):4} ids   contains target: {hit}")

# -----------------------------------------------------------------------------#
# 5. THE WORKSPACE ENDPOINT — documented as workspace PUBLIC protocols
# -----------------------------------------------------------------------------#
banner("5. /v3/workspaces/<uri>/protocols")

if workspace_uri:
    ids = set()
    for page in range(0, 30):
        payload = probe(
            f"workspace p{page}",
            f"{BASE_URL}/v3/workspaces/{workspace_uri}/protocols",
            {"page_size": 100, "page_id": page},
        )
        if not payload:
            break
        batch = payload.get("items") or []
        ids.update(item["id"] for item in batch if "id" in item)
        if not (payload.get("pagination") or {}).get("next_page"):
            break
    seen["workspace_public"] = ids
    print(
        f"    {len(ids)} ids   contains target: {'YES' if target_id in ids else 'no'}"
    )
else:
    print("  skipped — no workspace uri resolved in probe 2")

# -----------------------------------------------------------------------------#
# 6. FILE MANAGER — documented route to workspace PRIVATE protocols
# -----------------------------------------------------------------------------#
banner("6. File Manager search — /v4/filemanager/search is a PUT, not a GET")

# content_types[]=1 restricts to protocols; content_id on each item IS the
# protocol id, which is what lets this join to the store.
FM_PARAMS = {"page_size": 100, "content_types[]": 1}


def put_probe(label: str, url: str, params: dict) -> dict | None:
    try:
        response = requests.put(url, headers=HEADERS, params=params)
        response.raise_for_status()
        payload = response.json()
    except Exception as err:  # noqa: BLE001
        print(f"  [FAIL] {label}: {type(err).__name__} {err}")
        return None
    print(f"  [ ok ] {label}  status_code={payload.get('status_code')}")
    return payload


put_probe("filemanager/search (PUT)", f"{BASE_URL}/v4/filemanager/search", FM_PARAMS)

fm_ids: set = set()
shown = False
for guid, name in folders:
    for page in range(0, 30):
        fm = probe(
            f"folder {name[:18]} p{page}",
            f"{BASE_URL}/v4/filemanager/folders/{guid}/search",
            {**FM_PARAMS, "page_id": page},
        )
        if not fm:
            break
        items = fm.get("items") or []
        if items and not shown:
            shown = True
            print(f"    item keys: {sorted(items[0].keys())}")
            print(f"    first item: {json.dumps(items[0], indent=2)[:800]}")
        fm_ids.update(
            i["content_id"]
            for i in items
            if i.get("type_id") == 1 and "content_id" in i
        )
        if not (fm.get("pagination") or {}).get("next_page"):
            break

if fm_ids:
    seen["filemanager"] = fm_ids
    hit = "YES" if target_id in fm_ids else "no"
    print(f"\n    {len(fm_ids)} protocol ids   contains target: {hit}")

# -----------------------------------------------------------------------------#
# 7. SET ARITHMETIC vs THE STORED PULL
# -----------------------------------------------------------------------------#
banner("7. What the nightly pull is missing")

if DUMP.exists():
    pulled = {p["id"] for p in json.loads(DUMP.read_text())}
    print(f"  stored dump: {len(pulled)} protocols\n")
    union: set = set()
    for name, ids in seen.items():
        union |= ids
        print(
            f"    {name:20} {len(ids):4}   "
            f"new vs dump: {len(ids - pulled):4}   missing from it: {len(pulled - ids)}"
        )
    print(f"\n  union of all probes : {len(union)}")
    print(f"  NOT in the dump     : {len(union - pulled)}  <-- the blind spot")
    print(f"  in dump, not in any : {len(pulled - union)}")
else:
    print(f"  no dump at {DUMP}")
