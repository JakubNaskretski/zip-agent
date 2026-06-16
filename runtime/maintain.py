"""Maintenance — remove, rename, and reconcile base sources cleanly.

The digest path is add/merge-only: re-ingesting a smaller tree never deletes the
files it omits ("absence ≠ deletion", so a partial re-upload can't lose your KB).
That safety leaves a gap — there was no clean way to actually *remove* or *rename*
a base source, and a file deleted straight out of the zip left its graph node
orphaned. This module is the deliberate "yes, change it" path:

- `reconcile(ws)` — the consistency sweep: drop graph nodes whose raw file is gone
  (e.g. someone deleted files in the zip), cascade their edges, and clean the work
  links that pointed at them. Explicit on purpose — never automatic, so it can't
  prune on a transient/partial state.
- `forget(ws, source, rel)` — remove one base file: its raw file + sidecar + its
  graph nodes + the work edges to them.
- `remove_source(ws, source)` — drop a whole source (files + shard + L1).
- `rename(ws, source, old, new)` — move a base file and re-point its nodes'
  `source_path` (node ids are stable, so work links keep resolving).

All of these are stdlib-only and reuse the existing shard read + index regen — no
parsing engine, not imported at boot. They edit the base shard the sanctioned way
(by `source_path`, the same scope the merge uses), so it stays consistent and a
later digest reads it fine. Run them when you mean to; they are rare, deliberate.
"""
from __future__ import annotations

import json

from . import index_gen, layout, navigate, work


# --------------------------------------------------------------------------- #
# deterministic shard write (matches graphbuilder.persistence ordering, stdlib-only
# so maintenance never pulls the parsing engine). Already-redacted nodes stay
# redacted — we only drop/repoint, never re-serialise text.
# --------------------------------------------------------------------------- #
def _key(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)


def _save_shard(ws, source: str, graph: dict) -> None:
    nodes = sorted((n for n in graph.get("nodes", []) if isinstance(n, dict)),
                   key=lambda n: str(n.get("id", "")))
    edges = sorted((e for e in graph.get("edges", []) if isinstance(e, dict)),
                   key=lambda e: (str(e.get("src", "")), str(e.get("type", "")), str(e.get("dst", ""))))
    out = {"version": 1, "nodes": nodes, "edges": edges,
           "unresolved": sorted(graph.get("unresolved", []) or [], key=_key),
           "errors": sorted(graph.get("errors", []) or [], key=_key)}
    ws.write_text(layout.graph_shard(source),
                  json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False))


def _prune_nodes(graph: dict, drop_ids: set) -> dict:
    """Drop ``drop_ids`` and any edge touching them, then any external stub left
    with no edges. Mutates and returns ``graph``."""
    nodes = [n for n in graph["nodes"] if n.get("id") not in drop_ids]
    valid = {n.get("id") for n in nodes}
    edges = [e for e in graph["edges"] if e.get("src") in valid and e.get("dst") in valid]
    used = {e.get("src") for e in edges} | {e.get("dst") for e in edges}
    nodes = [n for n in nodes if not (n.get("external") and n.get("id") not in used)]
    valid = {n.get("id") for n in nodes}
    graph["nodes"] = nodes
    graph["edges"] = [e for e in edges if e.get("src") in valid and e.get("dst") in valid]
    return graph


def _raw(source: str, rel: str) -> str:
    return f"{layout.raw_dir(source)}/{rel}"


# --------------------------------------------------------------------------- #
# operations
# --------------------------------------------------------------------------- #
def reconcile(ws, *, sources=None) -> dict:
    """Drop graph nodes whose raw source file is gone (the file↔graph consistency
    sweep — covers files deleted straight out of the zip), clean the work edges
    that pointed at them, and regenerate the indexes. Explicit by design."""
    summary, removed_refs = {}, []
    for source in (sources or navigate.present_sources(ws)):
        if source == "work":
            continue
        g = navigate.load_shard(ws, source)
        paths = {n.get("source_path") for n in g["nodes"] if n.get("source_path")}
        missing = {p for p in paths if not ws.exists(_raw(source, p))}
        if not missing:
            continue
        drop_ids = {n.get("id") for n in g["nodes"] if n.get("source_path") in missing}
        _prune_nodes(g, drop_ids)
        _save_shard(ws, source, g)
        removed_refs += [f"{source}:{nid}" for nid in drop_ids]
        summary[source] = {"files_missing": len(missing), "nodes_dropped": len(drop_ids)}
    cleaned = work.drop_edges_to(ws, removed_refs) if removed_refs else 0
    index_gen.regenerate(ws)
    return {"sources": summary, "work_edges_cleaned": cleaned}


def forget(ws, source: str, rel: str) -> dict:
    """Remove one base file: its raw file + text sidecar + its graph nodes (by
    ``source_path == rel``) + the work edges pointing at them. Then reindex."""
    g = navigate.load_shard(ws, source)
    drop_ids = {n.get("id") for n in g["nodes"] if n.get("source_path") == rel}
    _prune_nodes(g, drop_ids)
    _save_shard(ws, source, g)
    ws.remove(_raw(source, rel))
    ws.remove(_raw(source, rel) + ".txt")        # sidecar, if any
    cleaned = work.drop_edges_to(ws, [f"{source}:{nid}" for nid in drop_ids])
    index_gen.regenerate(ws)
    return {"source": source, "rel": rel, "nodes_dropped": len(drop_ids),
            "work_edges_cleaned": cleaned}


def remove_source(ws, source: str) -> dict:
    """Drop a whole source — its raw files, its graph shard, its L1 — and the work
    edges into it. Then reindex (L0 drops the source)."""
    g = navigate.load_shard(ws, source)
    removed_refs = [f"{source}:{n.get('id')}" for n in g["nodes"]]
    files = ws.listing(layout.raw_dir(source))
    for p in files:
        ws.remove(p)
    ws.remove(layout.graph_shard(source))
    ws.remove(layout.index_l1(source))
    cleaned = work.drop_edges_to(ws, removed_refs)
    index_gen.regenerate(ws)
    return {"source": source, "files_removed": len(files),
            "nodes_dropped": len(removed_refs), "work_edges_cleaned": cleaned}


def rename(ws, source: str, old_rel: str, new_rel: str) -> dict:
    """Move a base file (and its sidecar) to a new relative path and re-point its
    nodes' ``source_path`` so ``read_source`` finds it. Node ids are content/name
    based, not path based, so work links pointing at those nodes keep resolving.

    NOTE: a work note that cited the OLD raw *path* in `derived_from` will show as
    stale in `work.review` afterward — update it (this moves the base file + graph,
    not your notes' path citations)."""
    old_raw, new_raw = _raw(source, old_rel), _raw(source, new_rel)
    if not ws.exists(old_raw):
        raise FileNotFoundError(old_raw)
    ws.write_bytes(new_raw, ws.read_bytes(old_raw))
    ws.remove(old_raw)
    if ws.exists(old_raw + ".txt"):
        ws.write_bytes(new_raw + ".txt", ws.read_bytes(old_raw + ".txt"))
        ws.remove(old_raw + ".txt")
    g = navigate.load_shard(ws, source)
    repointed = 0
    for n in g["nodes"]:
        if n.get("source_path") == old_rel:
            n["source_path"] = new_rel
            repointed += 1
    if repointed:
        _save_shard(ws, source, g)
    index_gen.regenerate(ws)
    return {"source": source, "old": old_rel, "new": new_rel, "nodes_repointed": repointed}
