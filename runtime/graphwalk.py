"""Pure, dependency-free traversal over a graph shard.

A shard is the plain ``{"nodes": [...], "edges": [...], "unresolved": [...],
"errors": [...]}`` dict produced by the parsing engine and stored as
``graph/<source>.json``. Navigation reads that JSON with the stdlib and walks it
with the functions below — it imports **neither** ``graphbuilder`` nor the digest
adapters. That is deliberate: a question-answering session must not pay to import
the 60-file parsing engine it never uses. The heavy engine is for ingest only.

Every walk is bounded (an explicit depth/limit) and cycle-safe (a visited set),
so broken or self-referential metadata can never loop forever or flood stdout —
the same caps the sandbox needs to stay alive.

Node shape (only the keys navigation relies on): ``id`` (``"type/name"``),
``type``, ``label``, ``source_path`` (the file the node came from), ``external``
(a referenced-but-not-parsed stub). Edges: ``src``, ``dst``, ``type``.
"""
from __future__ import annotations

from typing import Optional


def node(graph: dict, node_id: str) -> Optional[dict]:
    """The node with ``id == node_id``, or ``None``."""
    for n in graph.get("nodes", []):
        if n.get("id") == node_id:
            return n
    return None


def find_nodes(graph: dict, text: str, *, types=None, limit: int = 25) -> list:
    """Nodes whose id-name or label contains ``text`` (case-insensitive).

    The entity lookup inside a shard: resolve a user's word to concrete nodes in
    code, instead of dumping every name into context. Exact id-name and label
    matches sort first, then substring matches. ``types`` (a set/iterable)
    restricts to those node types. Stubs (``external``) sort last."""
    t = text.casefold()
    types = set(types) if types else None
    exact, partial = [], []
    for n in graph.get("nodes", []):
        if types and n.get("type") not in types:
            continue
        name = n.get("id", "").split("/", 1)[-1]
        label = n.get("label") or ""
        hay_name, hay_label = name.casefold(), label.casefold()
        if t == hay_name or t == hay_label:
            exact.append(n)
        elif t in hay_name or t in hay_label:
            partial.append(n)
    ranked = exact + partial
    ranked.sort(key=lambda n: bool(n.get("external")))   # real nodes before stubs
    return ranked[:limit]


def neighbors(graph: dict, node_id: str, direction: str = "out",
              edge_type: Optional[str] = None) -> list:
    """``(edge_type, neighbour_id)`` pairs adjacent to ``node_id``.

    ``out`` follows ``src→dst`` edges from the node; ``in`` follows ``dst→src``
    edges into it. Optional ``edge_type`` filters by relationship."""
    out = []
    for e in graph.get("edges", []):
        if edge_type and e.get("type") != edge_type:
            continue
        if direction == "out" and e.get("src") == node_id:
            out.append((e.get("type"), e.get("dst")))
        elif direction == "in" and e.get("dst") == node_id:
            out.append((e.get("type"), e.get("src")))
    return out


def build_index(graph: dict) -> dict:
    """Precompute the adjacency + node metadata a walk needs, ONCE.

    Returns ``{"out": {src: [dst,...]}, "in": {dst: [src,...]}, "meta": {id: (type,label)}}``.
    Building this is O(V+E); pass it to :func:`walk` (``idx=``) to avoid rebuilding
    per call when you traverse a shard repeatedly in a session (the Session caches
    one per source). Covers the unfiltered case — an ``edge_type``-filtered walk
    rebuilds its own adjacency (rare)."""
    adj_out: dict = {}
    adj_in: dict = {}
    for e in graph.get("edges", []):
        adj_out.setdefault(e.get("src"), []).append(e.get("dst"))
        adj_in.setdefault(e.get("dst"), []).append(e.get("src"))
    meta = {n.get("id"): (n.get("type"), n.get("label")) for n in graph.get("nodes", [])}
    return {"out": adj_out, "in": adj_in, "meta": meta}


def walk(graph: dict, node_id: str, depth: int = 2, limit: int = 200,
         direction: str = "out", edge_type: Optional[str] = None,
         idx: Optional[dict] = None) -> dict:
    """Bounded, cycle-safe BFS from ``node_id`` — the multi-hop primitive.

    Returns ``{"nodes": [{"id", "type", "label", "depth"}, ...], "truncated": N}``.
    The start node is not included. ``direction`` is ``out`` / ``in`` / ``both``.
    ``limit`` caps returned nodes; nodes discovered past it are counted in
    ``truncated`` but not returned. Each entry carries ``type`` + ``label`` looked
    up once, so the caller can triage without further reads. Never hand-roll BFS
    over a graph — the depth/limit caps here are what keep the sandbox alive.

    Pass ``idx`` (from :func:`build_index`) to reuse a prebuilt adjacency across
    many walks on the same shard; without it, adjacency is built locally (so a
    one-off walk needs no setup). ``idx`` is ignored when ``edge_type`` is set.
    """
    if idx is not None and edge_type is None:
        adj_out, adj_in, meta = idx["out"], idx["in"], idx["meta"]
    else:
        adj_out, adj_in = {}, {}
        for e in graph.get("edges", []):
            if edge_type and e.get("type") != edge_type:
                continue
            adj_out.setdefault(e.get("src"), []).append(e.get("dst"))
            adj_in.setdefault(e.get("dst"), []).append(e.get("src"))
        meta = {n.get("id"): (n.get("type"), n.get("label")) for n in graph.get("nodes", [])}

    visited = {node_id}
    queue = [(node_id, 0)]
    result: list = []
    truncated = 0
    while queue:
        current, cur_depth = queue.pop(0)
        if cur_depth >= depth:
            continue
        nexts: list = []
        if direction in ("out", "both"):
            nexts += adj_out.get(current, [])
        if direction in ("in", "both"):
            nexts += adj_in.get(current, [])
        for nxt in nexts:
            if nxt in visited:
                continue
            visited.add(nxt)
            nxt_depth = cur_depth + 1
            if len(result) < limit:
                ntype, nlabel = meta.get(nxt, (None, None))
                result.append({"id": nxt, "type": ntype, "label": nlabel, "depth": nxt_depth})
            else:
                truncated += 1
            if nxt_depth < depth:
                queue.append((nxt, nxt_depth))
    return {"nodes": result, "truncated": truncated}


def dependents(graph: dict, node_id: str) -> list:
    """``(edge_type, source)`` for everything pointing AT ``node_id`` — impact analysis."""
    return [(e.get("type"), e.get("src")) for e in graph.get("edges", [])
            if e.get("dst") == node_id]


def dependencies(graph: dict, node_id: str) -> list:
    """``(edge_type, target)`` for everything ``node_id`` points at — its outgoing deps."""
    return [(e.get("type"), e.get("dst")) for e in graph.get("edges", [])
            if e.get("src") == node_id]


def type_counts(graph: dict) -> dict:
    """``{node_type: count}`` for the shard, sorted by type — the L0 summary input."""
    out: dict = {}
    for n in graph.get("nodes", []):
        if n.get("external"):
            continue                      # stubs are references, not held content
        out[n.get("type", "?")] = out.get(n.get("type", "?"), 0) + 1
    return dict(sorted(out.items()))
