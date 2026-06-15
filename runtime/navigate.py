"""Navigate — load graph slices and source fragments on demand (L1 → L2).

The agent routes a question through the L0 map (already in context), decides the
source and entity, then calls the helpers here to pull *only* what it needs:

* :func:`load_l1` — the per-source routing aid (node-type counts, naming
  conventions, how to resolve names in code). Loaded when routing into a source.
* :func:`load_shard` — the source's structure graph, as a plain dict, held in a
  Python variable for the duration of an answer (never printed into context).
* :func:`find_nodes` / :func:`walk` / :func:`neighbors` / … — pure traversal
  (re-exported from :mod:`runtime.graphwalk`; no heavy imports).
* :func:`read_source` / :func:`excerpt` — the verbatim file behind a node, or a
  match-positioned window of it (triage before reading a whole file).
* :func:`navigate` — a one-call convenience that ties resolve → neighborhood →
  excerpt. The agent may use it or compose the primitives itself.

Nothing here imports ``graphbuilder`` or the digest adapters: navigation is the
cheap, always-available path. Parsing/ingest is the heavy, on-demand path
(:mod:`runtime.ingest`).
"""
from __future__ import annotations

import json
import re
from typing import Optional

from . import graphwalk, layout

# re-export the pure traversal primitives so callers can `from runtime import navigate`
# and reach everything in one place.
node = graphwalk.node
find_nodes = graphwalk.find_nodes
neighbors = graphwalk.neighbors
walk = graphwalk.walk
dependents = graphwalk.dependents
dependencies = graphwalk.dependencies
type_counts = graphwalk.type_counts

_EMPTY = {"nodes": [], "edges": [], "unresolved": [], "errors": []}


def present_sources(ws) -> list:
    """Sources that actually have a shard in this agent's memory."""
    return [s for s in layout.SOURCES if ws.exists(layout.graph_shard(s))]


def load_shard(ws, source: str) -> dict:
    """The source's structure graph as a dict (empty graph if not ingested yet)."""
    path = layout.graph_shard(source)
    if not ws.exists(path):
        return dict(_EMPTY, nodes=[], edges=[], unresolved=[], errors=[])
    data = json.loads(ws.read_text(path))
    # tolerant of the bare/partial shapes persistence.from_json accepts
    return {k: list(data.get(k, []) or []) for k in ("nodes", "edges", "unresolved", "errors")}


def load_l1(ws, source: str) -> str:
    """The per-source L1 routing aid, or '' if none."""
    path = layout.index_l1(source)
    return ws.read_text(path) if ws.exists(path) else ""


def read_source(ws, source: str, node_obj: dict) -> Optional[str]:
    """The verbatim file a node came from (via its ``source_path``), or ``None``."""
    sp = node_obj.get("source_path") if isinstance(node_obj, dict) else None
    if not sp:
        return None
    path = f"{layout.raw_dir(source)}/{sp}"
    return ws.read_text(path) if ws.exists(path) else None


def excerpt(text: str, term: str, window: int = 400, max_hits: int = 3) -> list:
    """Up to ``max_hits`` match-positioned windows of ``text`` around ``term``.

    Triage a file before reading the whole thing — print only these windows, not
    the file. Case-insensitive; returns ``[]`` when the term is absent."""
    if not text or not term:
        return []
    out = []
    # match on the ORIGINAL text (case-insensitively) so the index aligns with the
    # slice — casefold() is not length-preserving (ß→ss, ligatures), so finding in a
    # casefolded copy and slicing the original drifts the window off the match.
    for m in re.finditer(re.escape(term), text, re.IGNORECASE):
        if len(out) >= max_hits:
            break
        a = max(0, m.start() - window // 2)
        b = min(len(text), m.end() + window // 2)
        out.append(("…" if a > 0 else "") + text[a:b] + ("…" if b < len(text) else ""))
    return out


def navigate(ws, source: str, query: str, *, depth: int = 1, limit: int = 40,
             max_hits: int = 5) -> dict:
    """One-call L2: resolve ``query`` to nodes in ``source``'s shard and return
    each hit with its bounded neighborhood. The cheap default; for a full answer
    follow up with :func:`read_source` / :func:`excerpt` on the chosen node.

    Returns ``{"source", "query", "match_count", "hits": [{"node", "neighborhood"}]}``.
    """
    g = load_shard(ws, source)
    matches = graphwalk.find_nodes(g, query)
    hits = []
    for n in matches[:max_hits]:
        nb = graphwalk.walk(g, n["id"], depth=depth, limit=limit, direction="both")
        hits.append({"node": n, "neighborhood": nb})
    return {"source": source, "query": query, "match_count": len(matches), "hits": hits}
