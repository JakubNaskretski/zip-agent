"""Persistence — save / load a built graph as JSON.

A built graph is the plain dict ``{"nodes", "edges", "unresolved", "errors"}``
returned by :func:`graphbuilder.build_graph`. Persisting it means a build can be
cached, shipped to another tool, diffed across commits, or reloaded without
re-parsing the whole ``force-app``.

The on-disk form is a small wrapper carrying a ``version`` so the format can
evolve::

    {"version": 1, "nodes": [...], "edges": [...], "unresolved": [...], "errors": [...]}

Output is **deterministic**: nodes are sorted by id and edges by
``(src, type, dst)``, so two builds of the same metadata produce byte-identical
files (clean diffs). Loading is tolerant — a bare ``{"nodes", "edges"}`` dict, or
one missing the optional ``unresolved`` / ``errors`` keys, still loads.

Confidentiality: this layer only serialises what is already in the graph (names
and structure, never field/record values). Node ids are org-derived, so keep any
file built from a real org out of the repo. The one value a node can carry is a
Confluence page's inline ``text`` body; pass ``redact_text=True`` to drop it from
the output (the CLI does this by default) so a plain build can't spill bodies.
"""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA_VERSION = 1
_GRAPH_KEYS = ("nodes", "edges", "unresolved", "errors")

# The one node attribute that holds free text rather than a name/structure: a
# Confluence page body, inlined by the extractor. Redactable on serialisation.
_INLINE_TEXT_ATTR = "text"


def _entry_key(entry) -> str:
    """Total order over unresolved/errors entries of any shape (they are plain
    dicts of strings today, but stay tolerant)."""
    return json.dumps(entry, sort_keys=True, ensure_ascii=False, default=str)


def _redact_node(n: dict) -> dict:
    """Return a copy of ``n`` with the inline body text dropped, flagged by
    ``text_redacted`` so a redacted page is distinguishable from a body-less one.
    Nodes carrying no inline text pass through unchanged (no copy)."""
    if not n.get(_INLINE_TEXT_ATTR):
        return n
    out = {k: v for k, v in n.items() if k != _INLINE_TEXT_ATTR}
    out["text_redacted"] = True
    return out


def to_jsonable(graph, redact_text: bool = False) -> dict:
    """Normalise ``graph`` to the versioned, deterministically-ordered dict that
    gets written to disk. Missing optional keys default to empty lists. Never
    mutates the input. With ``redact_text``, drop each node's inline ``text`` body
    (Confluence pages) so confidential page text never reaches the file."""
    graph = graph or {}
    raw_nodes = (n for n in graph.get("nodes", []) or [] if isinstance(n, dict))
    if redact_text:
        raw_nodes = (_redact_node(n) for n in raw_nodes)
    nodes = sorted(
        raw_nodes,
        key=lambda n: str(n.get("id", "")),
    )
    edges = sorted(
        (e for e in graph.get("edges", []) or [] if isinstance(e, dict)),
        key=lambda e: (str(e.get("src", "")), str(e.get("type", "")), str(e.get("dst", ""))),
    )
    return {
        "version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": edges,
        # sorted like nodes/edges so determinism holds for the whole file, even
        # when a build interleaves these (e.g. a parallel bundle build)
        "unresolved": sorted(graph.get("unresolved", []) or [], key=_entry_key),
        "errors": sorted(graph.get("errors", []) or [], key=_entry_key),
    }


def to_json(graph, indent: int = 2, redact_text: bool = False) -> str:
    """Serialise ``graph`` to a JSON string (deterministic ordering). With
    ``redact_text``, inline page bodies are dropped (see :func:`to_jsonable`)."""
    return json.dumps(
        to_jsonable(graph, redact_text=redact_text),
        indent=indent, sort_keys=True, ensure_ascii=False,
    )


def from_json(text: str) -> dict:
    """Parse a JSON string into a graph dict ``{nodes, edges, unresolved, errors}``.

    Tolerant of the bare or partial shapes described in the module docstring.
    The ``version`` wrapper key is dropped from the returned graph."""
    data = json.loads(text)
    if not isinstance(data, dict):
        return {k: [] for k in _GRAPH_KEYS}
    return {k: list(data.get(k, []) or []) for k in _GRAPH_KEYS}


def save_graph(graph, path, redact_text: bool = False) -> Path:
    """Write ``graph`` to ``path`` as JSON (creating parent dirs). Returns the Path.
    With ``redact_text``, inline page bodies are dropped (see :func:`to_jsonable`)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_json(graph, redact_text=redact_text), encoding="utf-8")
    return path


def load_graph(path) -> dict:
    """Read a graph previously written by :func:`save_graph` (or any compatible
    JSON file). Returns ``{nodes, edges, unresolved, errors}``."""
    return from_json(Path(path).read_text(encoding="utf-8"))
