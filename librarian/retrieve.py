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


def excerpt(lib, ku_id, text, width=120, max_hits=3) -> list:
    """Inspect a KU body around a search term WITHOUT printing the whole file.

    This is the triage tool to use BEFORE any full ``lib.read_body`` call.  It
    reads the body, finds up to ``max_hits`` match-positioned windows around the
    term(s) in ``text``, and returns them as a list of short strings.  Only call
    ``lib.read_body`` when the excerpts are not enough, and only for one KU per
    execution (MASTER_PROMPT §4.1 deep-dive protocol).

    Parameters
    ----------
    lib:
        The active :class:`~librarian.Librarian` instance.
    ku_id:
        The KU to inspect.  Raises :class:`LookupError` (with ``ku_id`` in the
        message) if the KU is not found in the manifest.
    text:
        Search phrase; individual tokens are matched independently (same
        tokenisation as :func:`search`).
    width:
        Characters of context on each side of the match (default 120 total
        window).
    max_hits:
        Maximum number of excerpt strings to return (default 3).

    Returns
    -------
    list of str
        Up to ``max_hits`` match-positioned excerpt strings.  For binary/office
        raw KUs whose body cannot be decoded as UTF-8, returns a single-element
        list with a ``"binary body — use the #text sidecar"`` marker so the
        caller is never left empty-handed and the call never raises.
    """
    ku = lib.get(ku_id)
    if ku is None:
        raise LookupError(f"excerpt: KU not found: {ku_id!r}")

    raw_bytes = lib.store.read(ku.path)

    # Detect binary bodies: try strict UTF-8 first.
    try:
        body = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        # Office raw KUs (and any other binary blob) — decode with replace so
        # the call never crashes; but signal clearly that a sidecar is better.
        body_replaced = raw_bytes.decode("utf-8", errors="replace")
        if "�" in body_replaced:
            return ["binary body — use the #text sidecar"]
        body = body_replaced

    terms = _TOK.findall(text or "")
    if not terms:
        return [body[:width].strip()] if body else []

    low = body.lower()
    results: list = []
    covered: set = set()   # character ranges already returned, to avoid overlapping windows

    for term in terms:
        tl = term.lower()
        start_search = 0
        while len(results) < max_hits:
            pos = low.find(tl, start_search)
            if pos < 0:
                break
            win_start = max(0, pos - width // 2)
            win_end = win_start + width
            # skip window if it substantially overlaps an already-returned one
            if not any(abs(win_start - cs) < width // 2 for cs in covered):
                covered.add(win_start)
                prefix = "…" if win_start > 0 else ""
                results.append(prefix + body[win_start:win_end].strip() + "…")
            start_search = pos + len(tl)

    if not results:
        # no term matched — return a leading snippet so the caller still gets context
        results.append(body[:width].strip())

    return results[:max_hits]


def resolve_name(con, text, limit=10) -> list:
    """Step-0 primitive for imprecise-name resolution.

    Normalize ``text`` (lowercase, collapse whitespace) then look it up in two
    ways:
      (a) exact match in ``entities.name_norm`` — direct bridge hit;
      (b) exact match in ``aliases.alias`` joined back to ``entities`` via
          ``entities.name = aliases.canonical``.

    The two result sets are unioned, deduplicated by canonical name, ranked by
    the number of DISTINCT KU ids that mention the canonical (descending), and
    capped at ``limit``.  A canonical reachable by multiple routes reports the
    best ``via`` in priority order: ``"exact"`` > ``"curated"`` > ``"label"`` >
    ``"mech"``.

    Returns a list of dicts::

        [{"name": <canonical entity name>, "kus": <int>, "via": <str>}, ...]

    Empty list when nothing matches — **never a fuzzy guess**.  Feed the winner
    into :func:`find_entity`, :func:`cross_source`, or the graph helpers.

    Docstring: step-0 primitive for imprecise names; feed the winner into
    find_entity/cross_source/graph helpers.
    """
    q = " ".join(text.lower().split())
    if not q:
        return []

    _VIA_RANK = {"exact": 0, "curated": 1, "label": 2, "mech": 3}

    # Gather candidates: name → (ku_count, best_via)
    candidates: dict = {}

    # (a) exact entities.name_norm match
    rows = con.execute(
        "SELECT name, COUNT(DISTINCT ku_id) AS n FROM entities WHERE name_norm=? "
        "GROUP BY name",
        (q,),
    ).fetchall()
    for name, n in rows:
        best = candidates.get(name)
        if best is None or _VIA_RANK["exact"] < _VIA_RANK[best[1]]:
            candidates[name] = (n, "exact")

    # (b) alias match → join back to entities
    rows = con.execute(
        "SELECT e.name, COUNT(DISTINCT e.ku_id) AS n, a.via "
        "FROM aliases a JOIN entities e ON e.name = a.canonical "
        "WHERE a.alias=? "
        "GROUP BY e.name, a.via",
        (q,),
    ).fetchall()
    for name, n, via in rows:
        best = candidates.get(name)
        if best is None or _VIA_RANK.get(via, 99) < _VIA_RANK[best[1]]:
            # preserve the highest ku count seen for this canonical
            existing_n = candidates[name][0] if best else 0
            candidates[name] = (max(n, existing_n), via)
        elif best is not None:
            # same or worse via — keep count updated
            candidates[name] = (max(n, best[0]), best[1])

    # Also handle curated aliases whose canonical is NOT in entities (glossary tier)
    # — fetch just from aliases table without the entities join.
    # We want to surface these too; ku count = 0 (no entity bridge hits).
    rows_curated = con.execute(
        "SELECT canonical, via FROM aliases WHERE alias=?", (q,)
    ).fetchall()
    for canonical, via in rows_curated:
        if canonical not in candidates:
            candidates[canonical] = (0, via)

    if not candidates:
        return []

    # Sort: primary = ku_count descending, secondary = via rank ascending,
    # tertiary = name for stable ordering.
    def _sort_key(item):
        name, (n, via) = item
        return (-n, _VIA_RANK.get(via, 99), name)

    ranked = sorted(candidates.items(), key=_sort_key)
    return [{"name": name, "kus": n, "via": via}
            for name, (n, via) in ranked[:limit]]


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
