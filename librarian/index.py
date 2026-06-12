"""Search index — the cross-source entity bridge + full-text search, built as a
single serialized SQLite KU.

Derived and rebuildable (I13) from all active KUs. **Source-agnostic**: every
source's KUs join here by shared entity names, which is what turns cross-source
questions into O(1) lookups ("which <source> items mention X?"). Today only
Salesforce feeds it; when Jira/Confluence/Mule land, they join automatically.

The index is one `.sqlite` file holding two tables:
  - `entities(name, name_norm, ku_id, source, kind)` — the bridge
  - `docs` FTS5 over (title, entities, body) — keyword/prose search
It is stored as the body of a single KU (`agent:index/search`, tier=indexes) and
rebuilt via `rebuild_indexes()`. Idempotent: a logical hash of the KB state drives
the no-op, so an unchanged KB doesn't churn the index.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from .digest._progress import EVERY as _EVERY
from .schema import KnowledgeUnit, content_hash

INDEX_ID = "agent:index/search"
INDEX_PATH = "kb/indexes/search.sqlite"
_FTS_SKIP_KINDS = {"index", "graph"}        # don't full-text the derived blobs
_BODY_CAP = 200_000


def _serialize(con) -> bytes:
    try:
        return con.serialize()              # Python 3.11+, SQLite w/ deserialize
    except (AttributeError, sqlite3.OperationalError):   # pragma: no cover
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tf:
            tmp = Path(tf.name)
        dst = sqlite3.connect(tmp)
        con.backup(dst)
        dst.close()
        data = tmp.read_bytes()
        tmp.unlink(missing_ok=True)
        return data


def load_sqlite(data: bytes):
    """Open serialized index bytes as an in-memory connection."""
    con = sqlite3.connect(":memory:")
    try:
        con.deserialize(data)               # Python 3.11+
    except (AttributeError, sqlite3.OperationalError):   # pragma: no cover
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tf:
            tmp = Path(tf.name)
        tmp.write_bytes(data)
        disk = sqlite3.connect(tmp)
        disk.backup(con)
        disk.close()
        tmp.unlink(missing_ok=True)
    return con


def build_index(lib, progress=None):
    """Return (sqlite_bytes, logical_hash) for the current KB state.

    ``progress`` (callable, e.g. ``print``): one-line count every ``EVERY``
    KUs during the dominant FTS population loop, plus a compact final line."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE entities(name TEXT, name_norm TEXT, ku_id TEXT, source TEXT, kind TEXT)")
    con.execute("CREATE INDEX ix_ent ON entities(name_norm)")
    # contentless FTS: postings only — bodies already live as kb/ files, and a
    # stored copy roughly tripled the index (observed 40 MB on a 7k-KU org).
    # docmap carries the rowid -> KU identity that contentless tables can't.
    con.execute("CREATE VIRTUAL TABLE docs USING fts5("
                "title, entities, body, content='', tokenize='unicode61')")
    con.execute("CREATE TABLE docmap(rowid INTEGER PRIMARY KEY, ku_id TEXT, "
                "source TEXT, title TEXT, path TEXT)")
    sig = []
    indexed = 0
    for ku in sorted(lib.manifest.all(), key=lambda k: k.id):
        if ku.status != "active" or ku.id == INDEX_ID:
            continue
        ents = list(ku.entities or [])
        for ent in ents:
            con.execute("INSERT INTO entities VALUES(?,?,?,?,?)",
                        (ent, ent.lower(), ku.id, ku.source, ku.kind))
        if ku.kind not in _FTS_SKIP_KINDS:
            try:
                raw = lib.store.read(ku.path)
            except (OSError, FileNotFoundError):
                raw = b""
            if b"\x00" in raw[:4096]:
                raw = b""   # binary body (original docx/xlsx/pdf bytes) — its
                            # text sidecar KU is the search surface, not junk tokens
            body = raw.decode("utf-8", "replace")[:_BODY_CAP]
            if body or ents or ku.title:
                cur = con.execute("INSERT INTO docs(title, entities, body) VALUES(?,?,?)",
                                  (ku.title, " ".join(ents), body))
                con.execute("INSERT INTO docmap VALUES(?,?,?,?,?)",
                            (cur.lastrowid, ku.id, ku.source, ku.title, ku.path))
        sig.append([ku.id, sorted(ents), ku.content_hash])
        indexed += 1
        if progress is not None and indexed % _EVERY == 0:
            progress(f"index rebuild: {indexed} KUs indexed")
    if progress is not None:
        if indexed % _EVERY != 0 and indexed > 0:
            progress(f"index rebuild: done — {indexed} KUs indexed")
        elif indexed > 0:
            progress(f"index rebuild: done — {indexed} KUs indexed")
    con.commit()
    data = _serialize(con)
    con.close()
    logical = content_hash(json.dumps(sig, sort_keys=True, ensure_ascii=False))
    return data, logical


def rebuild_indexes(lib, author, rationale, progress=None):
    """Build the search index and commit it as a derived KU. Idempotent when the
    KB is unchanged (the logical hash drives the no-op). Returns the Report.

    ``progress`` (callable, e.g. ``print``): one-line count every ``EVERY``
    KUs during the FTS population loop, plus a compact final line."""
    data, logical = build_index(lib, progress=progress)
    ku = KnowledgeUnit(
        id=INDEX_ID, kind="index", tier="indexes", source="agent",
        path=INDEX_PATH, title="Search index (entity bridge + FTS)",
        confidence="VERIFIED", content_hash=logical,
    )
    txn = lib.begin(author, rationale)
    txn.ingest_ku(ku, body=data)
    return txn.commit()
