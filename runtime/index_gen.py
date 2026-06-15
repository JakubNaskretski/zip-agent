"""Index generation — project graph shards into the L0 / L1 routing layer.

The graph IS the index. After an ingest writes or merges a ``graph/<source>.json``
shard, :func:`regenerate` re-derives:

* ``index/L0.md`` — the knowledge map: one row per source (node/edge counts + the
  top node types) plus a routing line per source. Small by construction
  (a few hundred tokens) so it can sit in context for the whole session.
* ``index/L1/<source>.md`` — a per-source routing aid: the full node-type and
  top edge-type breakdown, how to resolve a specific name *in code*, and a small
  sample of the most-connected nodes for orientation. Bounded on purpose — it
  never lists every entity (a big org has thousands); the agent resolves
  specific names against the shard with :func:`runtime.navigate.find_nodes`.

These files are regenerated from the shards, never hand-edited — re-running
:func:`regenerate` after any shard change keeps them in sync.
"""
from __future__ import annotations

from . import graphwalk, layout, navigate

# One line describing what each source answers — keeps L0 useful without bloating it.
SOURCE_BLURB = {
    "salesforce": "objects, fields, Apex, triggers, flows, LWC/Aura/VF, layouts, permissions",
    "mule":       "Mule flows, connectors, API surface (APIkit), properties, DataWeave, MUnit",
    "jira":       "Jira issues, epics, sprints, releases, components (issue text is full-text only)",
    "confluence": "Confluence spaces and pages (page text is full-text only)",
    "docs":       "office documents — sections, sheets, tables, slides (structure only)",
    "work":       "your work layer — notes you authored + the edges you drew (incl. cross-source joins)",
}

_L1_SAMPLE = 30          # most-connected nodes shown for orientation
_L0_TOP_TYPES = 6        # node types listed inline per source in L0


def _degrees(graph: dict) -> dict:
    deg: dict = {}
    for e in graph.get("edges", []):
        deg[e.get("src")] = deg.get(e.get("src"), 0) + 1
        deg[e.get("dst")] = deg.get(e.get("dst"), 0) + 1
    return deg


def _fmt_types(counts: dict, top: int = None) -> str:
    items = list(counts.items())
    if top:
        items = items[:top]
    return ", ".join(f"{t}({c})" for t, c in items)


def render_l0(shards: dict) -> str:
    """``shards`` maps source → graph dict. Returns the L0 markdown."""
    lines = [
        "# L0 — Knowledge map",
        "",
        "_Route every question here first: pick the source, then load its "
        "`index/L1/<source>.md` and graph shard on demand. Don't dump the KB "
        "into context — resolve names and walk relationships in code._",
        "",
        "## Sources in this memory",
        "",
        "| Source | Nodes | Edges | Top node types |",
        "|--------|------:|------:|----------------|",
    ]
    present = [s for s in layout.SOURCES if s in shards]
    for s in present:
        g = shards[s]
        counts = graphwalk.type_counts(g)
        # rank types by count for the inline "top types"
        top = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
        lines.append(
            f"| {s} | {len(g.get('nodes', []))} | {len(g.get('edges', []))} "
            f"| {_fmt_types(top, _L0_TOP_TYPES)} |")
    if not present:
        lines.append("| _(none ingested yet)_ | | | |")
    lines += ["", "## Routing", ""]
    for s in present:
        lines.append(f"- **{s}** — {SOURCE_BLURB.get(s, '')}")
    lines += [
        "",
        "_Each source is a separate graph shard (`graph/<source>.json`). "
        "Structure/relationship questions → the shard; prose/keyword questions → "
        "full-text over the text sidecars (`kb/raw/<source>/`)._",
    ]
    return "\n".join(lines) + "\n"


def render_l1(source: str, graph: dict) -> str:
    """Per-source routing aid — bounded regardless of org size."""
    counts = graphwalk.type_counts(graph)
    edge_counts: dict = {}
    for e in graph.get("edges", []):
        edge_counts[e.get("type", "?")] = edge_counts.get(e.get("type", "?"), 0) + 1
    edge_top = dict(sorted(edge_counts.items(), key=lambda kv: (-kv[1], kv[0])))

    lines = [
        f"# L1 — {source}",
        "",
        f"_{SOURCE_BLURB.get(source, '')}_",
        "",
        f"Nodes: {len(graph.get('nodes', []))} · Edges: {len(graph.get('edges', []))} · "
        f"Unresolved refs: {len(graph.get('unresolved', []))} · "
        f"Extractor errors: {len(graph.get('errors', []))}",
        "",
        "## Node types",
        "",
        _fmt_types(dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))) or "_(none)_",
        "",
        "## Relationships (top edge types)",
        "",
        _fmt_types(edge_top, 20) or "_(none)_",
        "",
        "## Resolve a name (in code, not in context)",
        "",
        "```python",
        "from runtime import navigate",
        f'g = nav_shard = navigate.load_shard(ws, "{source}")',
        'hits = navigate.find_nodes(g, "PartialName")   # → matching node dicts',
        'nb   = navigate.walk(g, hits[0]["id"], depth=2, direction="both")',
        'src  = navigate.read_source(ws, "%s", hits[0])  # the verbatim file' % source,
        "```",
        "",
        f"## Most-connected nodes (orientation only — {_L1_SAMPLE} of "
        f"{len(graph.get('nodes', []))})",
        "",
    ]
    deg = _degrees(graph)
    ranked = sorted(
        (n for n in graph.get("nodes", []) if not n.get("external")),
        key=lambda n: (-deg.get(n.get("id"), 0), n.get("id", "")))
    for n in ranked[:_L1_SAMPLE]:
        label = n.get("label") or n.get("id", "").split("/", 1)[-1]
        lines.append(f"- `{n.get('id')}` — {label} ({deg.get(n.get('id'), 0)} edges)")
    if not ranked:
        lines.append("_(no nodes yet)_")
    return "\n".join(lines) + "\n"


def regenerate(ws) -> list:
    """Re-derive L0 + every present source's L1 from the shards on disk.

    Returns the list of index paths written. Called after an ingest changes a
    shard; each file is a single overlay write (no repack)."""
    sources = navigate.present_sources(ws)
    shards = {s: navigate.load_shard(ws, s) for s in sources}
    written = []

    ws.write_text(layout.INDEX_L0, render_l0(shards))
    written.append(layout.INDEX_L0)

    for s in sources:
        path = layout.index_l1(s)
        ws.write_text(path, render_l1(s, shards[s]))
        written.append(path)
    return written
