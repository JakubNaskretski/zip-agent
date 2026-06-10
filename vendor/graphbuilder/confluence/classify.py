"""Apply an agent's read-cold classification verdicts to a graph.

The agent (the LLM consumer — never this library) reads each page's content, names the
Salesforce entities it documents and its process type, and returns structured verdicts.
:func:`apply_classifications` validates and writes them back as ``documents`` edges
(``via="agent"`` with ``confidence``/``evidence``) plus page-node attrs
(``process_type`` / ``topics``) — deterministically, **mutating nothing**. A target id
that isn't in the graph is skipped and reported, never fabricated. The agent verdict is
authoritative: it supersedes a syntactic ``documents`` edge (from :func:`join`) for the
same (page, target) pair, so the graph keeps one ``documents`` edge per pair.

This is the write-back half of the agent surface; the read half is
``graphbuilder.find_nodes`` (resolve a name to a node id) + ``graphbuilder.node_text``
(read a node's content). No LLM lives here — the judgment is the agent's.
"""
from __future__ import annotations

import copy

_VALID_CONFIDENCE = {"low", "medium", "high"}


def apply_classifications(graph, verdicts):
    """Return ``(new_graph, report)`` with agent ``verdicts`` applied; inputs unchanged.

    ``verdicts``: iterable of ``{page_id, process_type?, topics?,
    documents?: [{target, confidence?, evidence?}]}``. ``report`` =
    ``{"applied": <edges upserted>, "updated_pages": <n>, "skipped": [{reason, ...}]}``.
    """
    g = copy.deepcopy(graph) if graph else {"nodes": [], "edges": [], "unresolved": [], "errors": []}
    by_id = {n["id"]: n for n in (g.get("nodes") or []) if isinstance(n, dict) and n.get("id")}

    # Split existing edges: documents edges go into a (src,dst)->edge map for upsert.
    doc_edges: dict = {}
    other_edges: list = []
    for e in g.get("edges") or []:
        if isinstance(e, dict) and e.get("type") == "documents":
            doc_edges[(e.get("src"), e.get("dst"))] = e
        else:
            other_edges.append(e)

    skipped: list = []
    applied = 0
    updated_pages = 0

    for v in verdicts or []:
        if not isinstance(v, dict):
            skipped.append({"reason": "verdict not a dict", "verdict": repr(v)[:80]})
            continue
        pid = v.get("page_id")
        page = by_id.get(pid)
        if page is None:
            skipped.append({"reason": "unknown page_id", "page_id": pid})
            continue

        changed = False
        if v.get("process_type"):
            page["process_type"] = str(v["process_type"])
            changed = True
        if v.get("topics"):
            page["topics"] = [str(t) for t in v["topics"] if t]
            changed = True
        if changed:
            updated_pages += 1

        for d in v.get("documents") or []:
            if not isinstance(d, dict):
                skipped.append({"reason": "document not a dict", "page_id": pid})
                continue
            target = d.get("target")
            if target not in by_id:
                skipped.append({"reason": "unknown target", "page_id": pid, "target": target})
                continue
            conf = d.get("confidence")
            edge = {
                "src": pid, "type": "documents", "dst": target, "via": "agent",
                "confidence": conf if conf in _VALID_CONFIDENCE else "medium",
            }
            if d.get("evidence"):
                edge["evidence"] = str(d["evidence"])[:500]
            doc_edges[(pid, target)] = edge          # agent supersedes any prior pair
            applied += 1

    g["edges"] = other_edges + list(doc_edges.values())
    return g, {"applied": applied, "updated_pages": updated_pages, "skipped": skipped}
