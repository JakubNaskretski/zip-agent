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


def build_index(lib):
    """Return (sqlite_bytes, logical_hash) for the current KB state."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE entities(name TEXT, name_norm TEXT, ku_id TEXT, source TEXT, kind TEXT)")
    con.execute("CREATE INDEX ix_ent ON entities(name_norm)")
    con.execute("CREATE VIRTUAL TABLE docs USING fts5("
                "ku_id UNINDEXED, source UNINDEXED, title, entities, body, tokenize='unicode61')")
    sig = []
    for ku in sorted(lib.manifest.all(), key=lambda k: k.id):
        if ku.status != "active" or ku.id == INDEX_ID:
            continue
        ents = list(ku.entities or [])
        for ent in ents:
            con.execute("INSERT INTO entities VALUES(?,?,?,?,?)",
                        (ent, ent.lower(), ku.id, ku.source, ku.kind))
        if ku.kind not in _FTS_SKIP_KINDS:
            try:
                body = lib.store.read(ku.path).decode("utf-8", "replace")[:_BODY_CAP]
            except (OSError, FileNotFoundError):
                body = ""
            con.execute("INSERT INTO docs VALUES(?,?,?,?,?)",
                        (ku.id, ku.source, ku.title, " ".join(ents), body))
        sig.append([ku.id, sorted(ents), ku.content_hash])
    con.commit()
    data = _serialize(con)
    con.close()
    logical = content_hash(json.dumps(sig, sort_keys=True, ensure_ascii=False))
    return data, logical


def rebuild_indexes(lib, author, rationale):
    """Build the search index and commit it as a derived KU. Idempotent when the
    KB is unchanged (the logical hash drives the no-op). Returns the Report."""
    data, logical = build_index(lib)
    ku = KnowledgeUnit(
        id=INDEX_ID, kind="index", tier="indexes", source="agent",
        path=INDEX_PATH, title="Search index (entity bridge + FTS)",
        confidence="VERIFIED", content_hash=logical,
    )
    txn = lib.begin(author, rationale)
    txn.ingest_ku(ku, body=data)
    return txn.commit()
