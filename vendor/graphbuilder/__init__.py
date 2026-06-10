"""graph-builder — parse a Salesforce force-app into a typed metadata graph.

    from graphbuilder import build_graph
    g = build_graph("path/to/force-app")     # {"nodes","edges","unresolved","errors"}

The build is two-pass (extract nodes + raw edges, then resolve each edge's
logical target into a node id); see `graphbuilder.core`. Extensible: each metadata
type is an Extractor under `graphbuilder/extractors/` (auto-discovered); references
are wired by Resolvers (`graphbuilder/resolvers.py`); traversal/display is
depth-limited and cycle-safe (`graphbuilder/model.py`).
"""
from . import salesforce, omnistudio, model, core, resolvers, analyze, persistence
from .extractors import build_graph, build_file, all_extractors
from .core import GraphBuilder, node, raw_edge, Extractor, Resolver
from .model import traverse, subgraph, neighborhood, find_cycles, NODE_TYPES, EDGE_TYPES
from .persistence import save_graph, load_graph, to_json, from_json
from .analyze import find_nodes, node_text

__all__ = [
    "salesforce", "omnistudio", "model", "core", "resolvers", "analyze", "persistence",
    "build_graph", "build_file", "all_extractors",
    "GraphBuilder", "node", "raw_edge", "Extractor", "Resolver",
    "traverse", "subgraph", "neighborhood", "find_cycles", "NODE_TYPES", "EDGE_TYPES",
    "save_graph", "load_graph", "to_json", "from_json",
    "find_nodes", "node_text",
]
