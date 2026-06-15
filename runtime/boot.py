"""Boot — connect to the deployed memory, load only the small routing layer.

Booting does **not** unpack the archive and does **not** hold an engine. It opens
the zip as a read-only base over a working folder (see :class:`~runtime.storage.Workspace`),
reads the manifest, and pulls only the resources the manifest marks
``on_startup`` + ``dest: context`` into memory — in practice the L0 knowledge map
and a couple of short instructions. Everything else (graph shards, raw files, the
parsing engine) is read or imported on demand, by slice, when a request actually
needs it.

Re-running :func:`boot` is a cheap idempotent reconnect: if the sandbox kernel
dies and takes the in-memory ``session`` with it, the on-disk zip + working folder
survive, so booting again rebuilds the handle and you continue from committed
state. It never repacks and never re-reads the KB into context.
"""
from __future__ import annotations

import json

from . import layout, navigate
from .storage import Workspace


class Session:
    """A live connection to the agent's memory: a :class:`Workspace`, the parsed
    manifest, and the small ``context`` resources loaded on boot. Holds no KB and
    no engine — navigation reads shards on demand through :mod:`runtime.navigate`."""

    def __init__(self, ws: Workspace, manifest: dict, context: dict):
        self.ws = ws
        self.manifest = manifest
        self.context = context        # {path: text} — the only thing pulled into memory on boot
        self._cache: dict = {}        # source -> (sig, graph, idx) — parse a shard once per session

    # -- the routing layer the host should print into the conversation ---------
    @property
    def l0(self) -> str:
        """The L0 knowledge map — route every question through this first."""
        return self.context.get(layout.INDEX_L0, "")

    def startup_context(self) -> str:
        """All on-startup context resources concatenated — paste into the chat once."""
        return "\n\n".join(self.context[p] for p in sorted(self.context))

    # -- navigation passthroughs (thin; compose the primitives directly too) ---
    def sources(self) -> list:
        """Which sources actually have a graph shard present."""
        return navigate.present_sources(self.ws)

    def l1(self, source: str) -> str:
        return navigate.load_l1(self.ws, source)

    def _shard_sig(self, source: str):
        # the overlay shard's mtime — changes when an ingest rewrites it, so the
        # cache invalidates; a base-only (zip) shard is immutable during a session.
        try:
            return (self.ws.work / layout.graph_shard(source)).stat().st_mtime_ns
        except OSError:
            return "base"

    def shard(self, source: str) -> dict:
        """The source's graph shard, parsed once per session (re-parsed only if an
        ingest rewrote it). Reuse instead of `navigate.load_shard` for repeat access."""
        sig = self._shard_sig(source)
        cached = self._cache.get(source)
        if cached and cached[0] == sig:
            return cached[1]
        g = navigate.load_shard(self.ws, source)
        self._cache[source] = (sig, g, None)      # adjacency built lazily on first walk
        return g

    def index(self, source: str) -> dict:
        """The cached adjacency index for a source's shard (built once)."""
        self.shard(source)
        sig, g, idx = self._cache[source]
        if idx is None:
            idx = navigate.build_index(g)
            self._cache[source] = (sig, g, idx)
        return idx

    def find(self, source: str, text: str, **kw) -> list:
        return navigate.find_nodes(self.shard(source), text, **kw)

    def navigate(self, source: str, query: str, *, depth: int = 1, limit: int = 40,
                 max_hits: int = 5) -> dict:
        """Resolve `query` in a source and return each hit with its neighborhood —
        reusing the cached shard + adjacency, so repeated calls don't re-parse."""
        g, idx = self.shard(source), self.index(source)
        matches = navigate.find_nodes(g, query)
        hits = [{"node": n,
                 "neighborhood": navigate.walk(g, n["id"], depth=depth, limit=limit,
                                                direction="both", idx=idx)}
                for n in matches[:max_hits]]
        return {"source": source, "query": query, "match_count": len(matches), "hits": hits}

    # -- the one place a whole zip is written ----------------------------------
    def export(self, out_zip: str) -> str:
        """Pack a NEW versioned zip (never the live ``memory.zip``) and return its path."""
        return str(self.ws.export(out_zip))

    def stats(self) -> dict:
        """Per-source node/edge counts from the shards on disk — durable committed state."""
        out = {}
        for s in self.sources():
            g = self.shard(s)
            out[s] = {"nodes": len(g.get("nodes", [])), "edges": len(g.get("edges", []))}
        return out


def boot(zip_path, work_dir) -> Session:
    """Open the deployed memory and load only the on-startup routing layer.

    ``zip_path`` is the retained ``memory.zip`` (read-only base); ``work_dir`` is
    the host working folder where changes accumulate. Idempotent — safe to re-run
    to reconnect after a kernel reset."""
    ws = Workspace(zip_path, work_dir)
    manifest = (json.loads(ws.read_text(layout.MANIFEST))
                if ws.exists(layout.MANIFEST) else {"resources": []})

    context: dict = {}
    for res in manifest.get("resources", []):
        if res.get("load_mode") == "on_startup" and res.get("dest") == "context":
            path = res.get("path")
            if path and ws.exists(path):
                context[path] = ws.read_text(path)

    # belt-and-suspenders: the L0 map is the agent's entry point — load it even if
    # a hand-assembled tree shipped without a perfect manifest entry for it.
    if layout.INDEX_L0 not in context and ws.exists(layout.INDEX_L0):
        context[layout.INDEX_L0] = ws.read_text(layout.INDEX_L0)

    return Session(ws, manifest, context)
