"""Extractor registry — auto-discovers `graphbuilder/extractors/*.py` modules.

Each extractor module defines a module-level `EXTRACTORS = [SomeExtractor(), ...]`.
Dropping a new module into this package is enough; it is picked up automatically
with no registry edit.
"""
from __future__ import annotations

import importlib
import pkgutil

from pathlib import Path

from ..core import GraphBuilder
from ..model import neighborhood
from ..resolvers import default_resolvers


def all_extractors() -> list:
    found = []
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_"):
            continue
        m = importlib.import_module(f".{mod.name}", __package__)
        found.extend(getattr(m, "EXTRACTORS", []))
    return found


def _builder(extra_resolvers=None) -> GraphBuilder:
    gb = GraphBuilder().register(*all_extractors())
    gb.register_resolver(*default_resolvers())
    if extra_resolvers:
        gb.register_resolver(*extra_resolvers)
    return gb


def build_graph(repo, extra_resolvers=None) -> dict:
    """Build the metadata graph for a force-app repo using all registered
    extractors + the default resolvers. Returns {nodes, edges, unresolved, errors}."""
    return _builder(extra_resolvers).build(repo)


def build_file(path, levels=None, types=None, repo=None, extra_resolvers=None) -> dict:
    """Digest a SINGLE metadata file into a graph ``{nodes, edges, unresolved, errors}``.

    Only the extractor that ``handles(path)`` runs; its edges are resolved with the
    default resolvers, so targets outside the file become **external stubs**.

    ``levels`` — how many levels deep to map, counted FROM the source file (1-based):
      - ``1`` keeps only the file's own nodes (e.g. an Apex class and its methods);
      - ``2`` also keeps everything one hop out (the objects/classes they reference);
      - ``3`` also keeps the next hop (e.g. those objects' fields); and so on.
      - ``None`` (default) applies no limit.
    Traversal is both-directions and cycle-safe. (Reaching e.g. an object's fields
    at level 3 needs ``repo`` context — a stubbed object carries no fields.)

    ``types`` — optional node-type allowlist (a type string or an iterable). The
    result is filtered to nodes of these types only, e.g. ``types="apexmethod"``
    on an Apex file maps just its methods, dropping the objects it touches. Edges
    are kept only between surviving nodes.

    ``repo`` — optional force-app root. When given, the file is digested in the
    context of the whole tree so its edges resolve to the REAL nodes in other
    files (not just stubs); ``levels`` then expand into that full graph. Without
    ``repo`` the digest is self-contained.

    Nothing raises: an unhandled file yields an empty graph; a failing extractor
    is reported in ``errors``.
    """
    path = Path(path)
    gb = _builder(extra_resolvers)

    # Level 1 = the node ids this one file defines (its own, non-stub nodes).
    file_graph = gb.build_files([path])
    seeds = [n["id"] for n in file_graph["nodes"] if not n.get("external")]

    # Full-repo context resolves edges to real cross-file nodes; otherwise the
    # self-contained single-file graph (off-file targets are external stubs).
    graph = gb.build(repo) if repo is not None else file_graph

    # levels are 1-based from the file, so hop budget = levels - 1. Distance is
    # undirected ("any"), so a level counts a hop regardless of edge direction
    # (e.g. class -> object via `references`, then object -> field via `field_of`).
    if levels is None:
        sub = graph if repo is None else neighborhood(graph, seeds, max_depth=None, direction="any")
    else:
        sub = neighborhood(graph, seeds, max_depth=max(int(levels) - 1, 0), direction="any")

    # optional node-type allowlist (keep edges only between surviving nodes)
    if types is not None:
        allow = {types} if isinstance(types, str) else set(types)
        keep = {n["id"] for n in sub["nodes"] if n.get("type") in allow}
        sub = {
            "nodes": [n for n in sub["nodes"] if n["id"] in keep],
            "edges": [e for e in sub["edges"] if e["src"] in keep and e["dst"] in keep],
        }

    return {
        "nodes": list(sub["nodes"]),
        "edges": list(sub["edges"]),
        # carry through the build-level diagnostics for the digested file/tree
        "unresolved": graph.get("unresolved", []),
        "errors": graph.get("errors", []),
    }
