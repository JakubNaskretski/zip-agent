"""Accumulate digest graphs across ingests instead of replacing them.

Every digest adapter writes ONE aggregate graph KU (``<source>:graph/<x>``)
holding the structure graph for the documents it just parsed. Writing it
naively REPLACES the prior graph, so an incremental re-ingest — a single new
file, or a re-parse of one edited file — silently drops every other document
from the graph (raw KUs + FTS survive; only the graph is lost).
:func:`merge_graphs` folds a freshly-parsed graph INTO the stored one so the
graph accumulates.

**Scope of an ingest** = the set of ``source_path`` values stamped on the new
graph's nodes (the engine stamps every node, descendants included). A re-ingest
supersedes exactly those source files' subgraphs — their nodes, the edges they
assert, and their build diagnostics — and leaves every other file untouched.
That honours the Librarian's "absence in a scoped re-ingest is not deletion"
guarantee: a file you did NOT re-ingest is never dropped, and a child/edge a
re-ingested file *dropped* does not survive as a phantom.

Two rules a naive node-union misses (both verified against the graph-builder
resolvers, which mint ``external: True`` stub nodes for any referenced record
absent from the current parse — see ``vendor/graphbuilder/resolvers.py``):

* **Never downgrade a real node to a stub.** A batch that references a record
  ingested in an earlier batch carries an ``external`` stub for it; that stub
  must not overwrite the real node — otherwise the stored body oscillates by
  ingest order and the Librarian's I9 content-hash idempotency breaks.
* **A re-ingested record's old edges are superseded, not merged.** Existing
  edges are kept only if their ``src`` was NOT re-ingested; the fresh parse
  carries the authoritative edge set for every source it touches, so a
  reference the record dropped does not linger.

Ordering is irrelevant here — the persistence layer re-sorts nodes/edges/
diagnostics on write — so re-ingesting an unchanged batch yields a
byte-identical body, which the Librarian reports as a no-op (I9).
"""
import json

_GRAPH_KEYS = ("nodes", "edges", "unresolved", "errors")


def empty_graph() -> dict:
    return {k: [] for k in _GRAPH_KEYS}


def load_existing(lib, graph_id, persistence) -> dict:
    """The stored aggregate graph as a dict, or an empty graph if there is no
    ACTIVE graph KU yet. ``persistence`` is the graph-builder persistence module
    (the caller already imports it as ``_gb_persistence``). The active check
    avoids merging a retired/tombstoned body back into a fresh graph."""
    entry = lib.get(graph_id)
    if entry is None or getattr(entry, "status", "active") != "active":
        return empty_graph()
    body = lib.read_body(graph_id)
    if not body:
        return empty_graph()
    return persistence.from_json(body)


def _scope_paths(graph) -> set:
    return {sp for n in graph.get("nodes", []) or []
            if isinstance(n, dict) and (sp := n.get("source_path"))}


def _diag_key(entry) -> str:
    return entry if isinstance(entry, str) else json.dumps(
        entry, sort_keys=True, ensure_ascii=False)


def _merge_diag(existing_list, new_list, owner, reingested) -> list:
    """Keep existing diagnostics whose owner was NOT re-ingested, then add the
    new batch's, de-duplicated. ``owner(entry)`` yields the scope key; entries
    whose owner is in ``reingested`` are dropped from the existing side because
    the fresh parse re-asserts them (so a fixed error/ref disappears, while an
    unchanged re-ingest stays byte-identical and other files' diagnostics are
    retained). Entries with no resolvable owner are always kept (union)."""
    out, seen = [], set()

    def add(entry):
        k = _diag_key(entry)
        if k not in seen:
            seen.add(k)
            out.append(entry)

    for e in existing_list or []:
        o = owner(e)
        if o is not None and o in reingested:
            continue
        add(e)
    for e in new_list or []:
        add(e)
    return out


def merge_graphs(existing: dict, new: dict) -> dict:
    """Fold ``new`` (a freshly-parsed digest graph) into ``existing`` (the
    stored aggregate). Both are ``{nodes, edges, unresolved, errors}`` dicts;
    returns a new merged dict (inputs untouched)."""
    new_paths = _scope_paths(new)
    nw = {n["id"]: n for n in new.get("nodes", []) or []
          if isinstance(n, dict) and n.get("id")}

    # existing nodes belonging to a re-ingested source file -> their old
    # subgraph is superseded by the fresh parse and must be dropped wholesale
    # (catches deleted/renamed children and edges, even when an edited file's
    # content hash — and thus its node ids — changes).
    superseded = {n["id"] for n in existing.get("nodes", []) or []
                  if isinstance(n, dict) and n.get("id")
                  and n.get("source_path") in new_paths}

    nodes = {}
    for n in existing.get("nodes", []) or []:
        nid = n.get("id") if isinstance(n, dict) else None
        if nid and nid not in superseded:
            nodes[nid] = n
    for nid, n in nw.items():
        keep = nodes.get(nid)
        if keep is not None and n.get("external") and not keep.get("external"):
            continue                       # never downgrade a real node to a stub
        nodes[nid] = n
    valid = set(nodes)

    seen, edges = set(), []

    def add_edge(ed):
        if not isinstance(ed, dict):
            return
        s, d, t = ed.get("src"), ed.get("dst"), ed.get("type")
        if s in valid and d in valid:
            k = (s, t, d)
            if k not in seen:
                seen.add(k)
                edges.append(ed)

    for ed in existing.get("edges", []) or []:
        if isinstance(ed, dict) and ed.get("src") in superseded:
            continue                       # the fresh parse re-asserts this source's edges
        add_edge(ed)
    for ed in new.get("edges", []) or []:
        add_edge(ed)

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "unresolved": _merge_diag(
            existing.get("unresolved"), new.get("unresolved"),
            lambda e: e.get("src") if isinstance(e, dict) else None, superseded),
        "errors": _merge_diag(
            existing.get("errors"), new.get("errors"),
            lambda e: e.get("path") if isinstance(e, dict) else None, new_paths),
    }
