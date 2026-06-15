"""The work layer — the agent's own nodes, edges, and files, wired into the KB.

This is the space the agent works IN, distinct from the ingested base sources:

- **Base sources** (`graph/<source>.json` + `kb/raw/<source>/`) are machine-built by
  ingest — the trusted source of record, never hand-edited.
- **The work layer** (`graph/work.json` + `kb/work/`) is what the agent authors: work
  files (notes, summaries, write-ups) and the edges it draws. It lives in its own shard
  only so it's identifiable and cleanable — NOT fenced off.

The two are one connected graph, joined by the **junction**: a work edge can point at any
base node, referenced as ``"<source>:<node_id>"`` (e.g. ``"salesforce:object/Account"``,
``"docs:docfile/ab12"``). Work nodes are referenced as ``"work:<type>/<name>"``. So the
agent can link its work to sources, and join sources to each other (a process node ↔ the
slide that shows it) — all without touching the pristine base shards.

Work nodes are usable and first-class. The only discipline (not a per-use caveat): on a
conflict the base source wins, and a work inference is not a parsed source fact.

Everything here is a single-file write to ``graph/work.json`` and/or a ``kb/work/`` file —
stdlib only, no engine, no repack. Sized for a small, agent-authored graph.
"""
from __future__ import annotations

import hashlib
import json

from . import layout, navigate

_FENCE = "---"
_EMPTY = {"nodes": [], "edges": [], "unresolved": [], "errors": []}


# --------------------------------------------------------------------------- #
# work-graph load / save (deterministic, single-file write)
# --------------------------------------------------------------------------- #
def _load(ws) -> dict:
    if not ws.exists(layout.WORK_SHARD):
        return {k: [] for k in _EMPTY}
    data = json.loads(ws.read_text(layout.WORK_SHARD))
    return {k: list(data.get(k, []) or []) for k in _EMPTY}


def _save(ws, g: dict) -> None:
    nodes = sorted(g.get("nodes", []), key=lambda n: n.get("id", ""))
    edges = sorted(g.get("edges", []),
                   key=lambda e: (e.get("src", ""), e.get("type", ""), e.get("dst", "")))
    out = {"version": 1, "nodes": nodes, "edges": edges, "unresolved": [], "errors": []}
    ws.write_text(layout.WORK_SHARD, json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False))


def _upsert_node(g: dict, node: dict) -> None:
    for i, n in enumerate(g["nodes"]):
        if n.get("id") == node["id"]:
            g["nodes"][i] = {**n, **node}
            return
    g["nodes"].append(node)


def _add_edge(g: dict, src: str, dst: str, kind: str, note=None) -> dict:
    for e in g["edges"]:
        if e.get("src") == src and e.get("dst") == dst and e.get("type") == kind:
            if note:
                e["note"] = note
            return e
    e = {"src": src, "dst": dst, "type": kind}
    if note:
        e["note"] = note
    g["edges"].append(e)
    return e


def _hash(ws, path: str):
    try:
        return hashlib.sha1(ws.read_bytes(path)).hexdigest()[:12]
    except FileNotFoundError:
        return None


# --------------------------------------------------------------------------- #
# ids / refs
# --------------------------------------------------------------------------- #
def ref(source: str, node_id: str) -> str:
    """A reference to a base node, the way the work graph points at it:
    ``ref("salesforce", "object/Account") == "salesforce:object/Account"``."""
    return f"{source}:{node_id}"


def _note_rel(rel: str) -> str:
    r = rel
    if r.startswith(layout.WORK_DIR + "/"):
        r = r[len(layout.WORK_DIR) + 1:]
    if r.endswith(".md"):
        r = r[:-3]
    return r


def _note_path(rel: str) -> str:
    r = _note_rel(rel)
    return f"{layout.WORK_DIR}/{r}.md"


def _note_id(rel: str) -> str:
    return f"work:note/{_note_rel(rel)}"


# --------------------------------------------------------------------------- #
# work files (notes) — markdown + a graph node + derived-from edges
# --------------------------------------------------------------------------- #
def write_note(ws, rel: str, body: str, *, title=None, author: str = "agent",
               derived_from=()) -> str:
    """Write a work note (`kb/work/<rel>.md`) and upsert its `note` node + `derived-from`
    edges to the sources it rests on. ``derived_from`` entries are node refs
    (``"<source>:<id>"``) or raw file paths (``"kb/raw/docs/x.docx"``); file paths are
    hashed for staleness (`review`). ``author`` is ``"agent"`` or ``"user"``. Returns
    the file path."""
    derived_from = list(derived_from)
    src_hashes = {p: _hash(ws, p) for p in derived_from if ws.exists(p)}
    fm = {"title": title or _note_rel(rel), "author": author,
          "derived_from": derived_from, "source_hashes": src_hashes}
    path = _note_path(rel)
    ws.write_text(path, f"{_FENCE}\n{json.dumps(fm, indent=2)}\n{_FENCE}\n\n{body}")

    g = _load(ws)
    nid = _note_id(rel)
    _upsert_node(g, {"id": nid, "type": "note", "label": title or _note_rel(rel),
                     "author": author, "source_path": path})
    # re-assert this note's derived-from edges (drop its old ones first)
    g["edges"] = [e for e in g["edges"]
                  if not (e.get("src") == nid and e.get("type") == "derived-from")]
    for d in derived_from:
        _add_edge(g, nid, d, "derived-from")
    _save(ws, g)
    return path


def read_note(ws, rel: str) -> dict:
    """``{"frontmatter": {...}, "body": "..."}`` for a stored work note (tolerant of a
    note missing/!= JSON frontmatter — never raises)."""
    text = ws.read_text(_note_path(rel))
    parts = text.split(_FENCE, 2)
    if text.startswith(_FENCE) and len(parts) == 3:
        try:
            return {"frontmatter": json.loads(parts[1]), "body": parts[2].lstrip("\n")}
        except json.JSONDecodeError:
            pass
    return {"frontmatter": {}, "body": text}


def list_notes(ws, prefix: str = "") -> list:
    """Paths of work notes under ``kb/work/<prefix>``."""
    root = f"{layout.WORK_DIR}/{prefix}" if prefix else layout.WORK_DIR
    return [p for p in ws.listing(root) if p.endswith(".md")]


def remove_note(ws, rel: str) -> None:
    """Delete a work note: its file (best-effort) + its node + its edges."""
    ws.remove(_note_path(rel))
    remove_node(ws, _note_id(rel))


# --------------------------------------------------------------------------- #
# free-standing nodes + the junction edges
# --------------------------------------------------------------------------- #
def add_node(ws, name: str, *, label=None, type: str = "concept", **attrs) -> str:
    """Add/update a work node (e.g. a "concept" you hang several source links off).
    ``name`` may be a bare name (→ ``work:<type>/<name>``) or a full ``work:…`` id.
    Returns the node id."""
    nid = name if name.startswith("work:") else f"work:{type}/{name}"
    g = _load(ws)
    _upsert_node(g, {"id": nid, "type": type, "label": label or name, **attrs})
    _save(ws, g)
    return nid


def link(ws, src: str, dst: str, *, kind: str = "relates-to", note=None) -> dict:
    """The junction: add an edge between ANY two node ids — work↔work, work↔source,
    or source↔source. Endpoints are work ids (``work:…``) or base refs
    (``"<source>:<id>"``). ``kind`` is your free-form relationship label. Idempotent."""
    g = _load(ws)
    e = _add_edge(g, src, dst, kind, note)
    _save(ws, g)
    return e


def unlink(ws, src: str, dst: str, *, kind=None) -> int:
    """Remove work edges between ``src`` and ``dst`` (any kind unless ``kind`` given).
    Returns the count removed."""
    g = _load(ws)
    before = len(g["edges"])
    g["edges"] = [e for e in g["edges"]
                  if not (e.get("src") == src and e.get("dst") == dst
                          and (kind is None or e.get("type") == kind))]
    removed = before - len(g["edges"])
    if removed:
        _save(ws, g)
    return removed


def remove_node(ws, node_id: str) -> None:
    """Remove a work node and every edge touching it."""
    g = _load(ws)
    g["nodes"] = [n for n in g["nodes"] if n.get("id") != node_id]
    g["edges"] = [e for e in g["edges"]
                  if e.get("src") != node_id and e.get("dst") != node_id]
    _save(ws, g)


# --------------------------------------------------------------------------- #
# navigation across the junction
# --------------------------------------------------------------------------- #
def links_of(ws, node_ref: str) -> list:
    """Every work edge touching ``node_ref``, from EITHER side. From a work note →
    its sources; from ``"salesforce:object/Account"`` → the work connected to it.
    Returns ``[{"other", "kind", "direction": "out"|"in", "note"}]``."""
    g = _load(ws)
    out = []
    for e in g["edges"]:
        if e.get("src") == node_ref:
            out.append({"other": e.get("dst"), "kind": e.get("type"),
                        "direction": "out", "note": e.get("note")})
        elif e.get("dst") == node_ref:
            out.append({"other": e.get("src"), "kind": e.get("type"),
                        "direction": "in", "note": e.get("note")})
    return out


def show(ws, node_ref: str) -> dict:
    """Resolve a ref to its node (and, for a base ref, its source content). A work ref
    returns the work node; ``"<source>:<id>"`` loads that base shard and reads the
    underlying source file. ``{"ref", "source", "node", "text"}``."""
    if node_ref.startswith("work:"):
        for n in _load(ws)["nodes"]:
            if n.get("id") == node_ref:
                return {"ref": node_ref, "source": "work", "node": n, "text": None}
        return {"ref": node_ref, "source": "work", "node": None, "text": None}
    if ":" in node_ref:
        source, nid = node_ref.split(":", 1)
        g = navigate.load_shard(ws, source)
        node = navigate.node(g, nid)
        text = navigate.read_source(ws, source, node) if node else None
        return {"ref": node_ref, "source": source, "node": node, "text": text}
    return {"ref": node_ref, "source": None, "node": None, "text": None}


# --------------------------------------------------------------------------- #
# keeping it tidy
# --------------------------------------------------------------------------- #
def review(ws, prefix: str = "") -> dict:
    """What to clean up: notes whose `derived_from` source files changed/vanished
    (`stale`), and edges referencing a deleted work node (`orphan_edges`)."""
    g = _load(ws)
    node_ids = {n.get("id") for n in g["nodes"]}
    stale = []
    for note_path in list_notes(ws, prefix):
        fm = read_note(ws, _note_rel(note_path))["frontmatter"]
        stored = fm.get("source_hashes") or {}
        changed = [s for s, old in stored.items() if _hash(ws, s) != old]
        if changed:
            stale.append({"note": note_path, "changed": changed})
    orphan = [e for e in g["edges"]
              if any(isinstance(x, str) and x.startswith("work:") and x not in node_ids
                     for x in (e.get("src"), e.get("dst")))]
    return {"stale": stale, "orphan_edges": orphan}


def prune_orphans(ws) -> int:
    """Drop work edges that reference a deleted work node. Returns the count removed."""
    g = _load(ws)
    node_ids = {n.get("id") for n in g["nodes"]}

    def ok(e):
        return not any(isinstance(x, str) and x.startswith("work:") and x not in node_ids
                       for x in (e.get("src"), e.get("dst")))

    before = len(g["edges"])
    g["edges"] = [e for e in g["edges"] if ok(e)]
    removed = before - len(g["edges"])
    if removed:
        _save(ws, g)
    return removed
