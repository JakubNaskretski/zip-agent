"""Curated notes — the agent's own findings, written as plain files.

In the lean model a curated note is just a markdown file under ``kb/curated/``.
There is no transaction and no blessed write API — the agent may ``ws.write_text``
(or ``open(...)``) a note directly. These helpers are the convenient default:
they add a small JSON frontmatter recording the note's title and the raw sources
it rests on, plus a content hash of each source *at write time*. That lets
:func:`review_needed` flag a note whose underlying source later changed — so a
stale finding can't masquerade as current (the one Librarian guarantee worth
keeping, done with a hash instead of a held engine).

    from runtime import notes
    notes.write_note(ws, "rfp/acme/req-001", body,
                     title="Req 1 — bulk import",
                     derived_from=["kb/raw/docs/rfp/requirements.docx"])
    notes.review_needed(ws)        # -> notes whose sources changed/vanished
"""
from __future__ import annotations

import hashlib
import json

CURATED = "kb/curated"
_FENCE = "---"


def _hash(ws, src_path: str):
    try:
        return hashlib.sha1(ws.read_bytes(src_path)).hexdigest()[:12]
    except FileNotFoundError:
        return None


def _rel(rel: str) -> str:
    path = rel if rel.startswith(CURATED + "/") else f"{CURATED}/{rel}"
    return path if path.endswith(".md") else path + ".md"


def write_note(ws, rel: str, body: str, *, title=None, derived_from=()) -> str:
    """Write a curated note (single-file write). ``derived_from`` is a list of raw
    file paths the note rests on; their content hashes are stored for staleness
    checks. Returns the path written."""
    derived_from = list(derived_from)
    fm = {
        "title": title or rel,
        "derived_from": derived_from,
        "source_hashes": {p: _hash(ws, p) for p in derived_from},
    }
    path = _rel(rel)
    ws.write_text(path, f"{_FENCE}\n{json.dumps(fm, indent=2)}\n{_FENCE}\n\n{body}")
    return path


def read_note(ws, rel: str) -> dict:
    """Return ``{"frontmatter": {...}, "body": "..."}`` for a stored note."""
    text = ws.read_text(_rel(rel))
    if text.startswith(_FENCE):
        _, fm, body = text.split(_FENCE, 2)
        try:
            return {"frontmatter": json.loads(fm), "body": body.lstrip("\n")}
        except json.JSONDecodeError:
            pass
    return {"frontmatter": {}, "body": text}


def list_notes(ws, prefix: str = "") -> list:
    """Paths of curated notes under ``kb/curated/<prefix>``."""
    root = f"{CURATED}/{prefix}" if prefix else CURATED
    return [p for p in ws.listing(root) if p.endswith(".md")]


def review_needed(ws, prefix: str = "") -> list:
    """Notes whose recorded sources have since changed or vanished.

    Returns ``[{"note", "changed": [...], "missing": [...]}, ...]`` — surface these
    proactively; a note built on a source that moved on may be out of date."""
    out = []
    for note_path in list_notes(ws, prefix):
        fm = read_note(ws, note_path)["frontmatter"]
        stored = fm.get("source_hashes") or {}
        changed, missing = [], []
        for src, old in stored.items():
            now = _hash(ws, src)
            if now is None:
                missing.append(src)
            elif now != old:
                changed.append(src)
        if changed or missing:
            out.append({"note": note_path, "changed": changed, "missing": missing})
    return out
