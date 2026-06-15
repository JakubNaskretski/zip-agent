"""On-disk layout of a lightweight agent.

A built agent is one ``memory.zip`` (the transport form) plus a ``MASTER_PROMPT.md``
beside it (pasted into the host's instructions field, never inside the zip). At
runtime the agent reads resources from the zip on demand and writes changes to a
working folder; the zip is repacked only on an explicit export. Nothing is held
in memory across the whole session, and a change is a single-file write — never a
whole-archive repack.

This module is the single source of truth for *where things live* inside that
zip / working folder, so the loader, the navigator, the ingest writer and the
builder all agree without copying string literals around.

Layout::

    manifest.json                  # what's inside + how to load it (read on boot)
    index/L0.md                    # the knowledge map — routed first, kept in context
    index/L1/<source>.md           # per-source routing aid — loaded on demand
    graph/<source>.json            # one structure-graph shard per source (on demand)
    kb/raw/<source>/<rel>          # verbatim source files (read by slice on demand)
    kb/raw/<source>/<rel>.txt      # plain-text sidecars alongside (the search surface)
    dev/                           # changelog / plan / session state (one file each)
    runtime/                       # this engine (small; imported on boot)
    graphbuilder/                  # the parsing engine (heavy; imported only at ingest)
    librarian/                     # digest adapters + schema (imported only at ingest)
    reference/wheelhouse/          # optional offline wheels (installed best-effort)

``<source>`` is one of: ``salesforce``, ``mule``, ``jira``, ``confluence``, ``docs``.
"""
from __future__ import annotations

# the lean runtime's own manifest — a distinct name from the legacy engine's
# KU ``manifest.json`` so both can ship in one zip during the transition.
MANIFEST = "agent_manifest.json"

INDEX_DIR = "index"
INDEX_L0 = "index/L0.md"


def index_l1(source: str) -> str:
    """Per-source L1 routing aid, e.g. ``index/L1/salesforce.md``."""
    return f"index/L1/{source}.md"


def graph_shard(source: str) -> str:
    """Per-source structure-graph shard, e.g. ``graph/salesforce.json``."""
    return f"graph/{source}.json"


def raw_dir(source: str) -> str:
    """Root of a source's verbatim files, e.g. ``kb/raw/salesforce``."""
    return f"kb/raw/{source}"


# The five sources an agent can hold. The label is the shard/index/dir segment.
SOURCES = ("salesforce", "mule", "jira", "confluence", "docs")

# The work layer — the agent's OWN editable overlay: a graph shard it hand-authors
# (notes + the edges it draws, including cross-source joins) and its work files.
# Kept distinct from the base sources above only so it's identifiable and cleanable
# — not fenced off; its edges point at base node refs to join the two (see runtime.work).
WORK_SHARD = "graph/work.json"
WORK_DIR = "kb/work"
