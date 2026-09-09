# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from dataclasses import asdict, dataclass, replace
from graphlib import CycleError, TopologicalSorter

from utils.constants import (
    PIPELINE_HASH_FIELDS,
    PIPELINE_METADATA_FIELDS,
    PROTOCOL_CONTENT,
    PROTOCOL_HISTORY,
)
from utils.store import connect


# -----------------------------------------------------------------------------#
# Error handling
# -----------------------------------------------------------------------------#
class UnresolvedProtocolError(ValueError):
    """The DAG names protocols the store holds no active version for."""

    def __init__(self, unresolved: list[str]):
        self.unresolved = unresolved
        super().__init__(f"no active protocol for: {', '.join(unresolved)}")


class NodeMismatchError(ValueError):
    """The graph and the node table disagree about which nodes exist."""

    def __init__(self, unnamed: list[str], orphaned: list[str]):
        self.unnamed, self.orphaned = unnamed, orphaned
        super().__init__(
            f"the graph names nodes that run no protocol: {unnamed}; "
            f"and these run a protocol but are not in the graph: {orphaned}"
        )


class PipelineCycleError(ValueError):
    """The graph loops, so the pipeline cannot run."""

    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        super().__init__(f"the graph is not acyclic: {' -> '.join(cycle)}")


class AmbiguousProtocolError(ValueError):
    """One name answers for more than one active protocol."""

    def __init__(self, ambiguous: dict[str, list[str]]):
        self.ambiguous = ambiguous
        super().__init__(
            "these names each resolve to several active protocols, "
            f"name them by protocol_uid instead: {ambiguous}"
        )


# -----------------------------------------------------------------------------#
# CREATE COMPOSITION TEMPLATE
# -----------------------------------------------------------------------------#


@dataclass(frozen=True)
class PipelineArtefact:
    # --- hashed (HASH_FIELDS) ---------------------------------------------- #
    guid: str
    title: str
    # None until the pipeline is pinned to a manifest; a base template is not.
    manifest_hash: str | None
    root: str | None  # starting material/ sample type
    # node id -> its successors. Node ids are the pipeline's own, not protocols',
    # so one protocol may run at several points in one graph.
    DAG: dict
    # node id -> the protocol it runs, named however the template author wrote it
    nodes: dict
    # node id -> that protocol's hash — empty until hydrate_pipeline runs
    node_hashes: dict
    # --- retained, never hashed (METADATA_FIELDS) -------------------------- #
    created_on: int
    creator: dict | str | None = None

    def to_dict(self) -> dict:
        """Full artefact as a plain dict — the stored/metadata-bearing form."""
        return asdict(self)

    def hashable(self) -> dict:
        """Only the fields HASH_FIELDS declares — the form that gets hashed."""
        return {field: getattr(self, field) for field in PIPELINE_HASH_FIELDS}

    def metadata(self) -> dict:
        """Get meta data fields"""
        return {field: getattr(self, field) for field in PIPELINE_METADATA_FIELDS}


# -----------------------------------------------------------------------------#
# HYDRATE A PIPELINE
# -----------------------------------------------------------------------------#
def successors(value) -> list[str]:
    """A node's successors. A lone one is written as a bare string in YAML."""
    if value is None:
        return []
    return [str(value)] if isinstance(value, str) else [str(node) for node in value]


def normalize_dag(dag: dict) -> dict[str, list[str]]:
    """Node -> successors, sorted.

    A fork is parallel and conditional, so which branch was written first is not
    information — and the DAG is hashed, so left alone it would reach the hash.
    """
    return {str(node): sorted(successors(value)) for node, value in dag.items()}


def dag_nodes(dag: dict) -> list[str]:
    """Every node the graph names, keys and successors alike, first seen first."""
    seen = dict.fromkeys(str(node) for node in dag)
    for value in dag.values():
        seen.update(dict.fromkeys(successors(value)))
    return list(seen)


def validate_dag(dag: dict, nodes: dict) -> None:
    """Every node runs exactly one protocol, and the graph terminates."""
    named, graph = set(nodes), set(dag_nodes(dag))
    if named != graph:
        raise NodeMismatchError(sorted(graph - named), sorted(named - graph))
    try:
        # Successors, where TopologicalSorter expects predecessors. The order it
        # would produce is reversed; a cycle is a cycle either way, and the cycle
        # is all this asks for.
        TopologicalSorter(normalize_dag(dag)).prepare()
    except CycleError as cycle:
        raise PipelineCycleError(list(cycle.args[1])) from cycle


def active_protocol_aliases(
    db: str,
    protocol_content: str = PROTOCOL_CONTENT,
    protocol_history: str = PROTOCOL_HISTORY,
) -> dict[str, set[str]]:
    """Every name an active protocol answers to -> the hashes that name reaches.

    A DAG may name a protocol by `protocol_uid`, `protocol_guid` or the bare
    `protocol_id`. The bare id is the one that collides across sources, so this
    returns a set per name and leaves the decision to the caller.
    """
    aliases: dict[str, set[str]] = {}
    with connect(db, read_only=True) as conn:
        rows = conn.execute(
            "SELECT content.protocol_uid, content.protocol_id, "
            "content.protocol_guid, content.hash "
            f"FROM {protocol_history} history "
            f"JOIN {protocol_content} content ON content.hash = history.hash "
            "WHERE history.deprecated_at IS NULL"
        )
        for *names, digest in rows:
            for name in names:
                aliases.setdefault(str(name), set()).add(digest)
    return aliases


def hydrate_pipeline(pipeline: PipelineArtefact, db: str) -> PipelineArtefact:
    """Resolve each node's protocol to the hash active right now.

    Returns a new artefact carrying `node_hashes`, which is hashed — so hydrating
    against a store where one protocol has moved on is a new pipeline version,
    which is the point. Two nodes running one protocol simply share a hash.
    """
    validate_dag(pipeline.DAG, pipeline.nodes)
    aliases = active_protocol_aliases(db)
    wanted = sorted({str(name) for name in pipeline.nodes.values()})

    if unresolved := [name for name in wanted if not aliases.get(name)]:
        raise UnresolvedProtocolError(unresolved)
    if ambiguous := {
        name: sorted(aliases[name]) for name in wanted if len(aliases[name]) > 1
    }:
        raise AmbiguousProtocolError(ambiguous)

    resolved = {name: next(iter(aliases[name])) for name in wanted}
    return replace(
        pipeline,
        node_hashes={
            str(node): resolved[str(name)] for node, name in pipeline.nodes.items()
        },
    )
