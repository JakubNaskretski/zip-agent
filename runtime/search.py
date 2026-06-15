"""On-demand keyword search over the text surfaces — no held index.

Navigation answers structure/relationship questions via the graph. This answers
the *prose / keyword* questions ("how is bulk import handled", "which docs mention
retries") by scanning the text surfaces on demand and returning ranked snippets:

* office documents — the plain-text sidecars (``kb/raw/docs/*.txt``);
* Jira / Confluence — the raw issue/page JSON (their text lives there);
* Salesforce / Mule — the raw source files (search code/config by keyword).

There is **no persisted index and nothing held in memory** between queries — each
call reads only the candidate files for that query. That keeps a question-answering
session light (the alternative, building a full inverted index at boot, is exactly
the heavy work the lean model avoids). For very large corpora this is a linear
scan; it is sized for RFP-scale document sets, and the cap below bounds the work.

The agent is free to grep the working folder itself; this is the convenient
ranked default, not the only way.
"""
from __future__ import annotations

import re

from . import layout

_TOK = re.compile(r"[a-zA-Z0-9_]+", re.UNICODE)

# text-bearing extensions per source root we are willing to scan
_SCAN_EXT = (".txt", ".json", ".cls", ".trigger", ".xml", ".js", ".html",
             ".md", ".dwl", ".raml", ".yaml", ".yml")
_MAX_BYTES = 512 * 1024     # skip absurdly large single files (bounds work)


def _tokens(text: str) -> list:
    return [t.casefold() for t in _TOK.findall(text)]


def _snippet(text: str, terms, window: int = 200) -> str:
    # match on the original text (case-insensitively) so the slice aligns with the
    # match — casefold() is not length-preserving (see runtime.navigate.excerpt).
    for t in terms:
        m = re.search(re.escape(t), text, re.IGNORECASE)
        if m:
            a = max(0, m.start() - window // 2)
            b = min(len(text), m.end() + window // 2)
            return ("…" if a > 0 else "") + text[a:b].replace("\n", " ") + ("…" if b < len(text) else "")
    return text[:window].replace("\n", " ")


def _candidates(ws, source) -> list:
    sources = [source] if source else list(layout.SOURCES)
    out = []
    for s in sources:
        if s == "work":
            continue                     # work files live in kb/work, handled below
        for rel in ws.listing(layout.raw_dir(s)):
            if rel.endswith(_SCAN_EXT):
                out.append((s, rel))
    # the agent's work layer (kb/work) — searched unless a specific base source was asked for
    if source in (None, "work"):
        for rel in ws.listing(layout.WORK_DIR):
            if rel.endswith(_SCAN_EXT):
                out.append(("work", rel))
    return out


def search(ws, query: str, *, source=None, k: int = 8) -> list:
    """Up to ``k`` ranked hits for ``query`` across the text surfaces.

    Returns ``[{"source", "path", "score", "snippet"}, ...]`` sorted by score.
    ``source`` scopes to one source (e.g. ``"docs"``). Scoring is term-frequency
    over the query's distinct terms — simple, transparent, and good enough to
    triage which file to read next."""
    terms = list(dict.fromkeys(_tokens(query)))
    if not terms:
        return []
    hits = []
    for s, rel in _candidates(ws, source):
        try:
            data = ws.read_bytes(rel)
        except FileNotFoundError:
            continue
        if len(data) > _MAX_BYTES:
            continue
        text = data.decode("utf-8", errors="replace")
        toks = _tokens(text)
        if not toks:
            continue
        tf = {}
        for tok in toks:
            tf[tok] = tf.get(tok, 0) + 1
        score = sum(tf.get(t, 0) for t in terms)
        # require at least one term present, and reward breadth (distinct terms hit)
        present = sum(1 for t in terms if t in tf)
        if present == 0:
            continue
        score += present * 2
        hits.append({"source": s, "path": rel, "score": score,
                     "snippet": _snippet(text, terms)})
    hits.sort(key=lambda h: (-h["score"], h["path"]))
    return hits[:k]
