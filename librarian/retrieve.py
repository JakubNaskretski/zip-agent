"""Retrieve / ASK primitives over the search index (entity bridge + FTS).

The agent composes these with the source-specific graph queries (e.g. Salesforce
`who_calls` / `grants_on`) to answer questions:
  - **graph** = exact relationships within a source,
  - **entity bridge** = cross-source joins by shared name,
  - **FTS** = keyword / prose search.

Usage:
    from librarian import retrieve, index
    index.rebuild_indexes(lib, "dev", "build the search index")   # after a digest
    con = retrieve.open_index(lib)
    retrieve.find_entity(con, "AccountUpdater")     # -> KUs mentioning it (any source)
    retrieve.cross_source(con, "MeterPointService") # -> {source: [ku_id, ...]}
    retrieve.search(con, "bulk import retry")       # -> ranked KUs w/ snippets
"""
from __future__ import annotations

import re

from .index import INDEX_ID, load_sqlite


def open_index(lib):
    """Load the serialized search index into an in-memory connection."""
    body = lib.read_body(INDEX_ID)
    if body is None:
        raise LookupError(
            "no search index yet — run: from librarian import rebuild_indexes; "
            "rebuild_indexes(lib, author, rationale)")
    return load_sqlite(body)


def find_entity(con, name) -> list:
    """Every KU whose `entities` includes `name` (case-insensitive, any source)."""
    rows = con.execute(
        "SELECT DISTINCT ku_id, source, kind FROM entities WHERE name_norm=? ORDER BY ku_id",
        (name.lower(),)).fetchall()
    return [{"ku_id": r[0], "source": r[1], "kind": r[2]} for r in rows]


def cross_source(con, name) -> dict:
    """The cross-source join: `name` grouped by which source mentions it."""
    out: dict = {}
    for r in find_entity(con, name):
        out.setdefault(r["source"], []).append(r["ku_id"])
    return out


def entity_like(con, prefix, limit=20) -> list:
    """Entity names starting with `prefix` — for autocomplete / disambiguation."""
    rows = con.execute(
        "SELECT DISTINCT name FROM entities WHERE name_norm LIKE ? ORDER BY name LIMIT ?",
        (prefix.lower() + "%", limit)).fetchall()
    return [r[0] for r in rows]


_TOK = re.compile(r"\w+", re.UNICODE)


def _fts_query(text) -> str:
    toks = _TOK.findall(text or "")
    return " OR ".join(f'"{t}"' for t in toks)


def search(con, text, k=10, source=None) -> list:
    """Full-text search over KU title/entities/body, ranked by BM25."""
    q = _fts_query(text)
    if not q:
        return []
    sql = ("SELECT ku_id, source, title, snippet(docs, 4, '[', ']', '…', 10), bm25(docs) "
           "FROM docs WHERE docs MATCH ?")
    args = [q]
    if source:
        sql += " AND source = ?"
        args.append(source)
    sql += " ORDER BY bm25(docs) LIMIT ?"
    args.append(k)
    rows = con.execute(sql, args).fetchall()
    return [{"ku_id": r[0], "source": r[1], "title": r[2], "snippet": r[3], "score": r[4]}
            for r in rows]
