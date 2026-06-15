"""Retrieve / ASK primitives over the search index (entity bridge + FTS).

The agent composes these with the source-specific graph queries (e.g. Salesforce
`who_calls` / `grants_on`) to answer questions:
  - **graph** = exact relationships within a source,
  - **entity bridge** = cross-source joins by shared name,
  - **FTS** = keyword / prose search.

Usage:
    from librarian import retrieve
    con = retrieve.open_index(lib)                  # built fresh from the live KB
    retrieve.find_entity(con, "AccountUpdater")     # -> KUs mentioning it (any source)
    retrieve.cross_source(con, "MeterPointService") # -> {source: [ku_id, ...]}
    retrieve.search(con, "bulk import retry", lib=lib)  # ranked KUs w/ snippets

The index (a :class:`~librarian.index.MemIndex`) is assembled in memory from the
live files on every ``open_index`` call, so it is always current — no rebuild or
persistence step is needed.
"""
from __future__ import annotations

import math
import re

from .index import _toks, build_index


def open_index(lib):
    """Build the in-memory search index from the live KB and return it.

    Always succeeds (no persisted index to be missing): the :class:`MemIndex`
    is assembled fresh from the manifest + kb/ files, so it reflects the current
    state of the knowledge base on every call."""
    return build_index(lib)


def find_entity(con, name) -> list:
    """Every KU whose `entities` includes `name` (case-insensitive, any source)."""
    target = name.lower()
    seen: set = set()
    out: list = []
    for (_n, name_norm, ku_id, source, kind) in con.entities:
        if name_norm == target and ku_id not in seen:
            seen.add(ku_id)
            out.append({"ku_id": ku_id, "source": source, "kind": kind})
    out.sort(key=lambda r: r["ku_id"])
    return out


def cross_source(con, name) -> dict:
    """The cross-source join: `name` grouped by which source mentions it."""
    out: dict = {}
    for r in find_entity(con, name):
        out.setdefault(r["source"], []).append(r["ku_id"])
    return out


def entity_like(con, prefix, limit=20) -> list:
    """Entity names starting with `prefix` — for autocomplete / disambiguation."""
    pre = prefix.lower()
    names = {name for (name, name_norm, _id, _s, _k) in con.entities
             if name_norm.startswith(pre)}
    return sorted(names)[:limit]


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

    # Build the lookup tables from the entity bridge.
    ku_by_name: dict = {}        # name -> set(ku_id)
    norm_to_names: dict = {}     # name_norm -> set(name)
    for (name, name_norm, ku_id, _src, _kind) in con.entities:
        ku_by_name.setdefault(name, set()).add(ku_id)
        norm_to_names.setdefault(name_norm, set()).add(name)

    # Gather candidates: name → (ku_count, best_via)
    candidates: dict = {}

    # (a) exact entities.name_norm match
    for name in norm_to_names.get(q, set()):
        n = len(ku_by_name.get(name, set()))
        best = candidates.get(name)
        if best is None or _VIA_RANK["exact"] < _VIA_RANK[best[1]]:
            candidates[name] = (n, "exact")

    # (b) alias match → join back to entities (alias == q AND canonical in bridge)
    for (alias, canonical, via) in con.aliases:
        if alias != q or canonical not in ku_by_name:
            continue
        name = canonical
        n = len(ku_by_name[name])
        best = candidates.get(name)
        if best is None or _VIA_RANK.get(via, 99) < _VIA_RANK[best[1]]:
            existing_n = best[0] if best else 0
            candidates[name] = (max(n, existing_n), via)
        else:
            # same or worse via — keep the higher ku count, retain best via
            candidates[name] = (max(n, best[0]), best[1])

    # (c) curated/glossary aliases whose canonical is NOT already a candidate
    # (e.g. a glossary canonical with no entity-bridge hit) — surface at ku count 0.
    for (alias, canonical, via) in con.aliases:
        if alias != q:
            continue
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

    OR-semantics over the query tokens (a doc matches if it contains ANY token),
    scored with Okapi BM25 (k1=1.2, b=0.75) over the in-memory inverted index.
    Higher ``score`` = more relevant.

    Pass ``lib`` to get match-positioned ``snippet`` strings (read from the KU
    bodies on demand); without it, results carry titles only — the index stores
    no body text to quote."""
    qtoks = _toks(text)
    if not qtoks:
        return []

    k1, b = 1.2, 0.75
    N = con.N
    avgdl = con.avgdl or 1

    # candidate docs = union of postings for the query tokens
    candidates: set = set()
    for t in qtoks:
        candidates.update(con.postings.get(t, ()))
    if not candidates:
        return []

    scored: list = []
    for idx in candidates:
        doc = con.docs[idx]
        if source and doc["source"] != source:
            continue
        tf, dl = doc["tf"], doc["dl"]
        score = 0.0
        for t in qtoks:
            f = tf.get(t, 0)
            if not f:
                continue
            df = con.df.get(t, 0)
            idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
            score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        if score > 0:
            scored.append((score, doc))

    scored.sort(key=lambda sd: (-sd[0], sd[1]["ku_id"]))
    terms = _TOK.findall(text or "")
    return [{"ku_id": doc["ku_id"], "source": doc["source"], "title": doc["title"],
             "snippet": _excerpt(lib, doc["path"], terms) if lib is not None else "",
             "score": score}
            for (score, doc) in scored[:k]]
