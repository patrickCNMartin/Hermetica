# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
from dataclasses import asdict, dataclass, replace
from graphlib import CycleError, TopologicalSorter

from seal.store import latest_protocols
from utils.constants import PIPELINE_HASH_FIELDS, PIPELINE_METADATA_FIELDS


# -----------------------------------------------------------------------------#
# Error handling
# -----------------------------------------------------------------------------#
class UnresolvedProtocolError(ValueError):
    """Nodes whose protocol has no active version, so nothing can be pinned."""

    def __init__(self, nodes: dict[str, str]):
        self.nodes = nodes
        super().__init__(
            "these nodes run a protocol with no active version — swap them for "
            f"an active protocol before locking: {nodes}"
        )


class UnsealedProtocolError(ValueError):
    """Nodes naming a protocol no pull ever sealed."""

    def __init__(self, nodes: dict[str, str]):
        self.nodes = nodes
        super().__init__(
            "these nodes name a protocol the store has never held — name "
            f"protocols by protocol_guid: {nodes}"
        )


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


# -----------------------------------------------------------------------------#
# CREATE COMPOSITION TEMPLATE
# -----------------------------------------------------------------------------#


@dataclass(frozen=True)
class PipelineArtefact:
    # --- hashed (HASH_FIELDS) ---------------------------------------------- #
    guid: str
    title: str
    # None on a template; set only on the pinned copy a lock carries.
    manifest_hash: str | None
    root: str | None  # starting material/ sample type
    # node id -> its successors. Node ids are the pipeline's own, not protocols',
    # so one protocol may run at several points in one graph.
    DAG: dict
    # node id -> the protocol_guid it runs. Never a hash: which version runs is
    # decided when a lock is built, not when a template is saved.
    nodes: dict
    # node id -> that protocol's hash — empty on a template, set by hydrate_pipeline
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


def check_nodes_sealed(pipeline: PipelineArtefact, db: str) -> None:
    """Refuse a node naming a guid the protocol store never held. An inactive
    protocol passes — a template outlives the versions it was drawn with."""
    known = latest_protocols(db, pipeline.nodes.values())
    if unsealed := {
        node: guid for node, guid in pipeline.nodes.items() if guid not in known
    }:
        raise UnsealedProtocolError(unsealed)


def hydrate_pipeline(pipeline: PipelineArtefact, db: str) -> PipelineArtefact:
    """Pin each node's protocol_guid to the hash active right now.

    This is the lock step, never the save step: the result carries
    `node_hashes`, which belong in a lock and not in the template store. A node
    whose protocol is not active has nothing to pin and is refused by name.
    """
    validate_dag(pipeline.DAG, pipeline.nodes)
    latest = latest_protocols(db, pipeline.nodes.values())
    if unresolved := {
        node: guid
        for node, guid in pipeline.nodes.items()
        if guid not in latest or latest[guid]["deprecated_at"] is not None
    }:
        raise UnresolvedProtocolError(unresolved)
    return replace(
        pipeline,
        node_hashes={
            node: latest[guid]["hash"] for node, guid in pipeline.nodes.items()
        },
    )
