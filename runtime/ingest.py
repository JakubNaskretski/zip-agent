"""Ingest — parse a source tree and write it into the agent's memory.

This is the heavy, on-demand path. Importing it pulls in the parsing engine
(``graphbuilder``) and the digest adapters — which a question-answering session
never needs, so it is **not** imported by ``runtime/__init__`` and is reached
only when the user actually ingests data:

    from runtime.ingest import digest_to_tree
    digest_to_tree(ws, "salesforce", "/mnt/data/force-app", progress=print)

What it does, all as single-file overlay writes (never a repack):

1. Parse the tree with the source's digest adapter (pure — nothing committed).
2. Write each parsed source file verbatim under ``kb/raw/<source>/`` (and any
   text sidecar under ``kb/text/<source>/``), so the original is retrievable.
3. Merge the freshly-parsed graph into the stored ``graph/<source>.json`` shard
   (via the existing :mod:`librarian.digest._graphmerge`, so a scoped re-ingest
   supersedes only the files it touched and never drops other files' subgraphs),
   and write the shard.
4. Regenerate ``index/L0.md`` + ``index/L1/<source>.md`` from the shards.

The merge and the graph vocabulary are reused verbatim from the existing engine —
only the *destination* changed: a file in the working folder instead of a
transactional Knowledge-Unit body.
"""
from __future__ import annotations

from . import index_gen, layout, navigate

# KU kinds that are NOT written as plain files here: the aggregate graph (we write
# the merged shard ourselves, from dg.graph) and the provenance tool record.
_SKIP_KINDS = {"graph", "tool"}


# --------------------------------------------------------------------------- #
# per-source adapters — each returns (graph_dict, [(KU, body), ...], redact_text)
# Parse paths are reused verbatim from librarian/digest; only the write target
# changes. Phase 1 ships Salesforce; the others are added in Phase 2.
# --------------------------------------------------------------------------- #
def _adapt_salesforce(src_dir, progress):
    from librarian.digest import graphbuilder as sf
    dg = sf.digest(src_dir, progress=progress)
    return dg.graph, dg.kus, False


_ADAPTERS = {
    "salesforce": _adapt_salesforce,
}


def digest_to_tree(ws, source: str, src_dir, *, progress=None) -> dict:
    """Parse ``src_dir`` for ``source`` and write it into ``ws``'s working folder.

    Returns a summary dict (files written, shard path, node/edge/unresolved/error
    counts, regenerated index paths). Re-ingesting unchanged content is effectively
    a no-op: the merge re-asserts the same subgraph and the deterministic shard
    serialisation yields a byte-identical file."""
    if source not in _ADAPTERS:
        raise ValueError(
            f"no ingest adapter for source {source!r}; "
            f"available: {', '.join(sorted(_ADAPTERS))}")

    # the adapter import is what puts the vendored engine on sys.path in the dev
    # repo (its own ImportError fallback), so import persistence only afterwards.
    graph, kus, redact = _ADAPTERS[source](src_dir, progress)

    from graphbuilder import persistence
    from librarian.digest import _graphmerge

    files_written = 0
    for ku, body in kus:
        if getattr(ku, "kind", None) in _SKIP_KINDS:
            continue
        path = getattr(ku, "path", None)
        if not path:
            continue
        if isinstance(body, bytes):
            ws.write_bytes(path, body)
        else:
            ws.write_text(path, body if isinstance(body, str) else str(body))
        files_written += 1

    existing = navigate.load_shard(ws, source)
    merged = _graphmerge.merge_graphs(existing, graph)
    ws.write_text(layout.graph_shard(source),
                  persistence.to_json(merged, redact_text=redact))

    index_paths = index_gen.regenerate(ws)

    return {
        "source": source,
        "files_written": files_written,
        "shard": layout.graph_shard(source),
        "nodes": len(merged.get("nodes", [])),
        "edges": len(merged.get("edges", [])),
        "unresolved": len(merged.get("unresolved", [])),
        "errors": len(merged.get("errors", [])),
        "indexes": index_paths,
    }
