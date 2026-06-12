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
    retrieve.search(con, "bulk import retry", lib=lib)  # ranked KUs w/ snippets
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


def _excerpt(lib, path, terms, width=90) -> str:
    """Match-positioned excerpt read from the KU's own kb/ file — the index is
    contentless (postings only), so snippets come from the source of truth."""
    try:
        body = lib.store.read(path).decode("utf-8", "replace")
    except (OSError, FileNotFoundError):
        return ""
    low = body.lower()
    pos = min((p for p in (low.find(t.lower()) for t in terms) if p >= 0),
              default=-1)
    if pos < 0:
        return body[:width].strip()
    start = max(0, pos - width // 2)
    return ("…" if start else "") + body[start:start + width].strip() + "…"


def search(con, text, k=10, source=None, lib=None) -> list:
    """Full-text search over KU title/entities/body, ranked by BM25.

    Pass ``lib`` to get match-positioned ``snippet`` strings (read from the KU
    bodies on demand); without it, results carry titles only — the index is
    contentless and stores no text to quote."""
    q = _fts_query(text)
    if not q:
        return []
    sql = ("SELECT m.ku_id, m.source, m.title, m.path, bm25(docs) "
           "FROM docs JOIN docmap m ON m.rowid = docs.rowid WHERE docs MATCH ?")
    args = [q]
    if source:
        sql += " AND m.source = ?"
        args.append(source)
    sql += " ORDER BY bm25(docs) LIMIT ?"
    args.append(k)
    rows = con.execute(sql, args).fetchall()
    terms = _TOK.findall(text or "")
    return [{"ku_id": r[0], "source": r[1], "title": r[2],
             "snippet": _excerpt(lib, r[3], terms) if lib is not None else "",
             "score": r[4]}
            for r in rows]
