"""Read-only queries over a built graph ``{nodes, edges}``.

Diagnostics computed after the graph is built; they never mutate it. The BFS
helpers reuse ``model.traverse`` and ``model.find_cycles``, so every query is
cycle-safe and bounded. They read only node ids/types and structural edges, never
field values, formulas, or other content.

Public API:
  - ``impact(graph, node_id, max_depth=None)`` — nodes that transitively DEPEND ON
    ``node_id`` (reverse/incoming edge direction).
  - ``orphans(graph, types=None)`` — node ids with no incoming edges (excluding
    ``external`` nodes).
  - ``permission_reachability(graph, target_id)`` — permissionset/profile ids that
    grant access to ``target_id`` directly or via a containing permset group.
  - ``graph_summary(graph)`` — counts of nodes/edges by type plus cycle/orphan totals.
  - ``find_nodes(graph, query, types=None, limit=20)`` — ranked id/label search; the
    retrieval entry point that resolves a name to concrete node id(s).
  - ``node_text(node, root=None)`` — a node's full text (inline ``text`` attr, or the
    file at its ``content`` pointer).
"""
from __future__ import annotations

import difflib
from collections import Counter
from pathlib import Path

from . import model

# Edge types whose target is a permission principal grant / group membership.
_GRANTS = "grants"
_CONTAINS = "contains"
_PRINCIPAL_TYPES = frozenset({"permissionset", "profile"})


def _nodes_by_id(graph):
    """``{id: node}`` map, tolerant of odd/missing node dicts."""
    out = {}
    for n in (graph or {}).get("nodes", []) or []:
        try:
            nid = n.get("id")
        except AttributeError:
            continue
        if nid is not None:
            out.setdefault(nid, n)
    return out


def impact(graph, node_id, max_depth=None):
    """Nodes that transitively DEPEND ON ``node_id``.

    Edges read "from depends on to", so the dependants of ``node_id`` are the
    nodes reachable by following edges in the **reverse (incoming)** direction.
    Returns a list of ``{"id": <id>, "depth": <int>}`` ordered by increasing
    depth (BFS order), excluding ``node_id`` itself. ``max_depth=None`` is
    unbounded but still cycle-safe. Returns ``[]`` for an unknown ``node_id``.
    """
    if not graph or node_id is None:
        return []
    if node_id not in _nodes_by_id(graph):
        return []
    out = []
    for nid, depth, _etype in model.traverse(
        graph, node_id, direction="in", max_depth=max_depth
    ):
        out.append({"id": nid, "depth": depth})
    return out


def orphans(graph, types=None):
    """Node ids with NO incoming edge, excluding nodes marked ``external`` True.

    An orphan is a node nothing else depends on (no edge points *at* it). Nodes
    flagged ``external`` (resolver-created stubs for things outside the repo,
    e.g. standard objects) are never reported. ``types`` optionally restricts the
    result to the given node type(s) — pass a string or an iterable of strings.
    """
    if not graph:
        return []
    nbyid = _nodes_by_id(graph)

    type_filter = None
    if types is not None:
        type_filter = {types} if isinstance(types, str) else set(types)

    has_incoming = set()
    for e in graph.get("edges", []) or []:
        dst = e.get("dst") if isinstance(e, dict) else None
        if dst is not None:
            has_incoming.add(dst)

    out = []
    for nid, n in nbyid.items():
        if nid in has_incoming:
            continue
        if n.get("external") is True:
            continue
        if type_filter is not None and n.get("type") not in type_filter:
            continue
        out.append(nid)
    return sorted(out)


def permission_reachability(graph, target_id):
    """Sorted permissionset/profile ids granting access to ``target_id``.

    A principal reaches ``target_id`` when:
      - it has a ``grants`` edge directly to ``target_id``, OR
      - it is a permissionset contained (``contains`` edge) by a permsetgroup
        whose member permissionset grants ``target_id`` (group-mediated).
    Returns a sorted, de-duplicated list of ``permissionset``/``profile`` node
    ids. Unknown / unspecified ``target_id`` yields ``[]``.
    """
    if not graph or target_id is None:
        return []
    nbyid = _nodes_by_id(graph)

    # Direct granters: principals with a `grants` edge straight at the target.
    granters = set()
    for e in graph.get("edges", []) or []:
        if not isinstance(e, dict):
            continue
        if e.get("type") != _GRANTS or e.get("dst") != target_id:
            continue
        src = e.get("src")
        if src is None:
            continue
        if nbyid.get(src, {}).get("type") in _PRINCIPAL_TYPES:
            granters.add(src)

    # Group-mediated: any permsetgroup that `contains` a granting permissionset
    # keeps that permissionset in the result set (it is itself a principal). This
    # also surfaces members reachable only through group membership.
    contained_principals = set()
    for e in graph.get("edges", []) or []:
        if not isinstance(e, dict):
            continue
        if e.get("type") != _CONTAINS:
            continue
        src, dst = e.get("src"), e.get("dst")
        if nbyid.get(src, {}).get("type") != "permsetgroup":
            continue
        if dst in granters and nbyid.get(dst, {}).get("type") in _PRINCIPAL_TYPES:
            contained_principals.add(dst)

    return sorted(granters | contained_principals)


def graph_summary(graph):
    """Summary dict: ``node_counts``, ``edge_counts``, ``cycle_count``, ``orphan_count``.

    ``node_counts`` / ``edge_counts`` map each type to its tally. ``cycle_count``
    uses ``model.find_cycles`` (purely diagnostic; traversal stays bounded
    regardless). ``orphan_count`` is the number of non-external nodes with no
    incoming edge.
    """
    graph = graph or {}
    node_counts = Counter()
    for n in graph.get("nodes", []) or []:
        if isinstance(n, dict):
            node_counts[n.get("type")] += 1

    edge_counts = Counter()
    for e in graph.get("edges", []) or []:
        if isinstance(e, dict):
            edge_counts[e.get("type")] += 1

    try:
        cycle_count = len(model.find_cycles(graph))
    except Exception:
        cycle_count = 0

    return {
        "node_counts": dict(node_counts),
        "edge_counts": dict(edge_counts),
        "cycle_count": cycle_count,
        "orphan_count": len(orphans(graph)),
    }


def _match_score(q: str, text: str) -> float:
    """Match strength of lowercase ``q`` against lowercase ``text``: exact 1.0 >
    prefix 0.9 > substring 0.75 > ``difflib`` ratio (kept only when >= 0.5)."""
    if not text:
        return 0.0
    if text == q:
        return 1.0
    if text.startswith(q) or q.startswith(text):
        return 0.9
    if q in text or text in q:
        return 0.75
    ratio = difflib.SequenceMatcher(None, q, text).ratio()
    return ratio if ratio >= 0.5 else 0.0


def find_nodes(graph, query, types=None, limit=20):
    """Rank nodes whose id-name or label matches ``query`` (case-insensitive).

    The retrieval entry point: resolve a name found in text (e.g. a Confluence page
    mentions "the Billing object") to concrete node id(s). ``types`` (a str or
    iterable) restricts the search; ``limit`` caps the result (``None``/0 = no cap).
    Deterministic — ties break by node id. Returns ``[{"id","type","label","score"}]``
    best-first, dependency-free (stdlib ``difflib``)."""
    if not graph or query is None:
        return []
    q = str(query).strip().lower()
    if not q:
        return []
    type_filter = {types} if isinstance(types, str) else (set(types) if types else None)

    scored = []
    for n in graph.get("nodes", []) or []:
        if not isinstance(n, dict):
            continue
        nid = n.get("id")
        if not nid or (type_filter is not None and n.get("type") not in type_filter):
            continue
        name = str(nid).split("/", 1)[-1]
        label = str(n.get("label") or name)
        score = max(_match_score(q, name.lower()), _match_score(q, label.lower()))
        if score > 0:
            scored.append((score, nid, n))

    scored.sort(key=lambda t: (-t[0], t[1]))
    lim = int(limit) if (limit and int(limit) > 0) else None
    return [
        {"id": nid, "type": n.get("type"), "label": n.get("label"), "score": round(score, 3)}
        for score, nid, n in (scored[:lim] if lim else scored)
    ]


def node_text(node, root=None) -> str:
    """Full text for a node: its inline ``text`` attr, else the file at its ``content``
    pointer (relative to ``root``, the bundle root). Tolerant — ``""`` on a missing
    attr/file or any read error."""
    if not isinstance(node, dict):
        return ""
    if node.get("text"):
        return str(node["text"])
    rel = node.get("content")
    if not rel:
        return ""
    try:
        return (Path(root) / rel if root else Path(rel)).read_text(encoding="utf-8")
    except Exception:
        return ""
