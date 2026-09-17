# -----------------------------------------------------------------------------#
# THE COMPOSE PORT — compose.db read-write, chronos.db read-only
# -----------------------------------------------------------------------------#
"""What outside tools may do with pipeline templates: list, read, save, retire,
and export a lock.

A template is the shape of a pipeline — its DAG and the protocol_guid each node
runs — and never a hash. Which protocol versions run is decided once, when a
lock is exported, against the protocols active at that moment."""

import uuid
from dataclasses import replace

from api.contract import InvalidRequestError, describe_intervals
from compose.compose import check_nodes_sealed, hydrate_pipeline
from compose.store import (
    InactivePipelineError,
    active_pipelines,
    build_pipeline_entry,
    get_pipelines,
    pipeline_from_entry,
    pipeline_intervals,
    retire_pipeline,
    write_pipeline,
)
from compose.templates import build_pipeline
from seal.seal import generate_lock, generate_pipeline_lock, generate_protocol_lock
from seal.store import latest_protocols
from utils.dates import as_iso, get_timestamp
from utils.hashing import decode_entry


# -----------------------------------------------------------------------------#
# ERRORS
# -----------------------------------------------------------------------------#
class UnknownPipelineError(ValueError):
    """A guid this store never minted."""

    def __init__(self, guid: str):
        self.guid = guid
        super().__init__(
            f"no pipeline {guid!r} exists; save without a guid to create one"
        )


# -----------------------------------------------------------------------------#
# SHAPING
# -----------------------------------------------------------------------------#
def describe(entry: dict, protocols: dict[str, dict]) -> dict:
    """A stored template as the API shows it, each node carrying its protocol's
    current state. An inactive protocol is still sent — the UI draws it in the
    DAG, marked inactive, so the user can swap it out."""
    nodes = decode_entry(entry["nodes"])
    return {
        "pipeline_guid": entry["pipeline_guid"],
        "hash": entry["hash"],
        "title": entry["title"],
        "root": entry["root"],
        "dag": decode_entry(entry["DAG"]),
        "nodes": {
            node: {
                "protocol_guid": guid,
                "status": (
                    "active" if protocols[guid]["deprecated_at"] is None else "inactive"
                ),
                "protocol_uid": protocols[guid]["protocol_uid"],
                "title": protocols[guid]["title"],
                "hash": protocols[guid]["hash"],
            }
            for node, guid in nodes.items()
        },
        "created_on": as_iso(entry["created_on"]) if entry["created_on"] else None,
        "creator": decode_entry(entry["creator"]),
        "valid_from": as_iso(entry["valid_from"]),
    }


def describe_all(entries: list[dict], protocol_db: str) -> list[dict]:
    """Describe templates with one protocol lookup for all their nodes."""
    guids = {
        guid for entry in entries for guid in decode_entry(entry["nodes"]).values()
    }
    protocols = latest_protocols(protocol_db, guids)
    return [describe(entry, protocols) for entry in entries]


def check_payload(payload) -> None:
    """Refuse a template body before it reaches the builder. Input is untrusted."""
    if not isinstance(payload, dict):
        raise InvalidRequestError(["the body must be a JSON object"])

    problems = []
    title = payload.get("title")
    if not (isinstance(title, str) and title.strip()):
        problems.append("`title` must be a non-empty string")

    dag = payload.get("dag")
    if not isinstance(dag, dict) or not all(
        isinstance(node, str)
        and (
            isinstance(after, str)
            or (isinstance(after, list) and all(isinstance(a, str) for a in after))
        )
        for node, after in dag.items()
    ):
        problems.append("`dag` must map node ids to a node id or a list of them")

    nodes = payload.get("nodes")
    if not isinstance(nodes, dict) or not all(
        isinstance(node, str) and isinstance(guid, str) and guid
        for node, guid in nodes.items()
    ):
        problems.append("`nodes` must map node ids to a protocol_guid")

    if not isinstance(payload.get("root"), (str, type(None))):
        problems.append("`root` must be a string or absent")
    if not isinstance(payload.get("creator"), (str, dict, type(None))):
        problems.append("`creator` must be a string, an object, or absent")

    if problems:
        raise InvalidRequestError(problems)


# -----------------------------------------------------------------------------#
# THE PORT
# -----------------------------------------------------------------------------#
def list_pipelines(db: str, protocol_db: str) -> list[dict]:
    """Every active template — the list a user picks from."""
    return describe_all(active_pipelines(db), protocol_db)


def get_pipeline(db: str, protocol_db: str, guid: str) -> dict:
    """One active template. Raises InactivePipelineError if none."""
    entries = active_pipelines(db, guid)
    if not entries:
        raise InactivePipelineError(guid)
    return describe_all(entries, protocol_db)[0]


def pipeline_versions(db: str, guid: str) -> dict:
    """Every version one template has held, oldest first — retired ones included."""
    intervals = pipeline_intervals(db, guid)
    if not intervals:
        raise UnknownPipelineError(guid)
    return {"pipeline_guid": guid, "versions": describe_intervals(intervals)}


def save_pipeline(
    db: str,
    protocol_db: str,
    payload: dict,
    guid: str | None = None,
    saved_at: int | None = None,
) -> dict:
    """Save a template: create one (no guid) or version an existing one (its guid).

    Records the DAG and node guids only. The guid is minted here and only here —
    a caller cannot invent one. A node naming a protocol the store never held is
    refused; an inactive one is kept, because a template outlives versions. A
    retired template may be saved again: it reopens as a new interval.
    """
    check_payload(payload)
    saved_at = saved_at if saved_at is not None else get_timestamp()

    if guid is None:
        guid, created_on = uuid.uuid4().hex, saved_at
    else:
        history = pipeline_intervals(db, guid)
        if not history:
            raise UnknownPipelineError(guid)
        # Authored once; an edit is a new version, not a new template.
        (first,) = get_pipelines(db, [history[0]["hash"]], with_blob=False)
        created_on = first.created_on

    spec = {
        "pipeline_guid": guid,
        "dag": payload["dag"],
        "nodes": payload["nodes"],
        "root": payload.get("root"),
    }
    built = build_pipeline(payload["title"], spec, created_on, payload.get("creator"))
    check_nodes_sealed(built, protocol_db)
    entry = build_pipeline_entry(built, saved_at)
    diff = write_pipeline(db, [entry], saved_at)

    status = next(key for key in ("new", "changed", "unchanged") if guid in diff[key])
    return {"pipeline_guid": guid, "hash": entry.hash, "status": status}


def retire(db: str, guid: str, retired_at: int | None = None) -> dict:
    """Take a template out of the list. Its content and history stay."""
    return {"pipeline_guid": guid, "hash": retire_pipeline(db, guid, retired_at)}


def export_lock(
    db: str, protocol_db: str, guid: str, exported_at: int | None = None
) -> dict:
    """The full lock for one saved template, pinned to the protocols active now.

    Takes a guid, not a DAG: an edited template must be saved before it can be
    exported, so every lock points at a template the store holds. Refuses, by
    node, any protocol no longer active — a lock is a guarantee.
    """
    exported_at = exported_at if exported_at is not None else get_timestamp()
    entries = active_pipelines(db, guid)
    if not entries:
        raise InactivePipelineError(guid)
    template = pipeline_from_entry(entries[0])

    pinned = hydrate_pipeline(template, protocol_db)
    protocols = generate_protocol_lock(
        sorted(set(pinned.node_hashes.values())), protocol_db, as_of=exported_at
    )
    pinned = replace(pinned, manifest_hash=protocols["manifest_hash"])
    pipeline = generate_pipeline_lock(
        [pinned], as_of=exported_at, provenance={"template_hash": entries[0]["hash"]}
    )
    return generate_lock(protocols, pipeline)
