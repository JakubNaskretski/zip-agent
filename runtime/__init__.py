"""runtime — the lightweight agent engine.

Boot connects to the deployed memory and loads only the small routing layer;
navigation reads graph shards and source slices on demand. Nothing heavy is
imported here: ingest (which pulls in the parsing engine) lives in
:mod:`runtime.ingest` and is imported only when data is actually ingested.

    from runtime import boot, navigate
    session = boot("/mnt/data/memory.zip", "/mnt/data/memory_work")
    print(session.l0)                       # the knowledge map → into context
    g = session.shard("salesforce")         # a graph shard → a Python variable
    hits = navigate.find_nodes(g, "Account")

The agent is free to write its own helper code against the working folder and the
shards — these helpers are the easy default, not a cage.
"""
from __future__ import annotations

from . import graphwalk, layout, navigate
from .boot import Session, boot
from .storage import Workspace

__all__ = [
    "boot", "Session", "Workspace",
    "navigate", "graphwalk", "layout",
]
