"""PDF document extractor — ``*.pdf``, backed by the OPTIONAL pure-python
``pypdf`` dependency (``pip install graph-builder[pdf]``).

The dependency is guarded exactly like the Apex extractor's tree-sitter
backend: when ``pypdf`` is not installed this module still imports, the
module-level ``_PYPDF`` flag stays ``None`` and ``handles()`` returns
``False`` — the extractor is INERT, so ``.pdf`` files are silently skipped
(no nodes, no error entries) and every other extractor is unaffected.

Emits the same ``docs`` vocabulary as the Word extractor: a ``docfile`` node
(content-hash id ``docfile/<sha1-12>``, filename as label, ``doc_type: "pdf"``,
``page_count``, ``structure`` tier attr) plus a ``docsection`` tree
(ordinal-stable ids ``docsection/<sha1-12>#<n>``) wired by ``contains``
(docfile -> top-level section) and ``child-of`` (section -> parent section).
Structure detection (see :mod:`graphbuilder.office` for the tier model):

  T1 DECLARED   the document outline (bookmarks) -> one section per entry,
      level = nesting depth, ``page_start``/``page_end`` (1-based, inclusive)
      derived from the bookmark targets: a section runs to the page before the
      next same-or-higher-level entry (so a parent's range spans its
      subsections, exactly like a Word parent's text spans its children).
      Section ``text`` = the extracted text of its page range — the deliberate
      content capture, like Word section bodies.
  T3 NONE       no outline -> the ``docfile`` alone, ``structure: "none"``,
      the whole document text on the node. Font-size heading heuristics are
      deliberately NOT v1 — PDF has no T2 tier; sections are never fabricated.

A scanned PDF (pages > 0 but no extractable text) gets ``needs_ocr: true`` and
no ``text`` attr — the raw file still carries the page images. An ENCRYPTED
PDF RAISES out of ``extract`` so the core records it in ``errors`` — never
silently skipped. Document-info author/creator/producer fields are NAMES and
are never read (only the title and the modification date) — anonymization by
default, like the docProps creator fields. References detected in the
extracted text (Jira keys / ``X__c`` API names / URLs) are ATTRS on the
docfile, never edges — same as docx/xlsx.
"""
from __future__ import annotations

import io
from pathlib import Path

from ..core import node, raw_edge
from ..office import detect_refs, file_id

# --- optional pypdf backend; absent -> the extractor is inert --------------- #
# Guarded like the apex tree-sitter import: never raises at import time. This
# module-level flag is authoritative — ``handles`` gates on it, so
# monkeypatching it to None makes the extractor decline every file.
_PYPDF = None
try:  # pragma: no cover - exercised by whichever environment is installed
    import pypdf as _PYPDF
except Exception:  # pragma: no cover - ImportError or a broken install
    _PYPDF = None


def _doc_meta(reader) -> tuple:
    """``(title, modified)`` from the document-info dictionary; modified as an
    ISO string. Author / Creator / Producer are personal/tool NAMES —
    deliberately never read (anonymization by default). Best-effort: malformed
    metadata yields empty strings, never an error."""
    title = modified = ""
    try:
        meta = reader.metadata
    except Exception:
        meta = None
    if meta is not None:
        try:
            title = (meta.title or "").strip()
        except Exception:
            pass
        try:
            md = meta.modification_date
            modified = md.isoformat() if md else ""
        except Exception:
            pass
    return title, modified


def _outline_marks(reader) -> list:
    """The outline flattened to ``(title, level, page_start)`` in outline
    order; level = nesting depth (1-based), page_start 1-based. An entry whose
    destination page can't be resolved (broken/external target) or that has no
    title is skipped — its children re-attach to the nearest surviving
    ancestor via the extractor's level stack."""
    marks: list = []

    def walk(items, level):
        for item in items:
            if isinstance(item, list):            # children of the previous entry
                walk(item, level + 1)
                continue
            try:
                pageno = reader.get_destination_page_number(item)
            except Exception:
                pageno = None
            title = str(getattr(item, "title", "") or "").strip()
            if pageno is None or not title:
                continue
            marks.append((title, level, pageno + 1))

    walk(reader.outline or [], 1)
    return marks


class PdfExtractor:
    source = "docs"

    def handles(self, path: Path) -> bool:
        # without pypdf the extractor is INERT: .pdf files are skipped
        # silently (never claimed, never errored) — documented module-level
        return _PYPDF is not None and path.suffix.lower() == ".pdf"

    def extract(self, path: Path):
        data = Path(path).read_bytes()
        fid = file_id(data)
        reader = _PYPDF.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # raise, never skip: the core records the file in ``errors``,
            # so an unreadable (possibly sensitive) document stays visible
            raise ValueError("encrypted PDF - not readable without a password")

        page_texts = [(p.extract_text() or "") for p in reader.pages]
        page_count = len(page_texts)
        # scanned: pages exist but extraction yields nothing — flag it instead
        # of pretending an empty text capture is knowledge
        needs_ocr = page_count > 0 and not any(t.strip() for t in page_texts)

        def span_text(start: int, end: int) -> str:
            """Extracted text of pages ``start..end`` (1-based, inclusive)."""
            return "\n".join(
                t for t in (pt.strip() for pt in page_texts[start - 1:end]) if t)

        # T1 sections from the outline: ordinal-stable ids, parentage via a
        # level stack (like docx); a section's page range runs to the page
        # before the next same-or-higher-level entry, so a parent spans its
        # subsections' pages — FTS over a section finds everything under it
        sections: list = []          # (ordinal, title, level, parent, start, end)
        stack: list = []             # [(level, ordinal)]
        marks = _outline_marks(reader)
        for n, (title, lvl, start) in enumerate(marks, 1):
            start = max(1, min(start, page_count or 1))
            end = page_count
            for _t2, l2, s2 in marks[n:]:
                if l2 <= lvl:
                    end = s2 - 1
                    break
            end = max(start, min(end, page_count))
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            parent = stack[-1][1] if stack else 0
            sections.append((n, title, lvl, parent, start, end))
            stack.append((lvl, n))

        # --- docfile node (identity + tier + the flat/preamble content) ---
        attrs = {"source": "docs", "doc_type": "pdf",
                 "structure": "declared" if sections else "none"}
        if page_count:
            attrs["page_count"] = page_count
        doc_title, modified = _doc_meta(reader)
        if doc_title:
            attrs["title"] = doc_title
        if modified:
            attrs["modified"] = modified
        if needs_ocr:
            attrs["needs_ocr"] = True
        else:
            # pages before the first bookmark belong to the docfile itself;
            # with no outline the whole document text lands there (T3)
            first = sections[0][4] if sections else page_count + 1
            preamble = span_text(1, first - 1) if page_count else ""
            if preamble:
                attrs["text"] = preamble
        # detected refs scan the WHOLE extracted text — attrs only, never edges
        refs = detect_refs("\n".join(page_texts))
        for key in ("jira_keys", "sf_names", "urls"):
            if refs.get(key):
                attrs[key] = refs[key]
        did = f"docfile/{fid}"
        nodes = [node(did, "docfile", path.name, **attrs)]

        edges: list = []
        for n, title, lvl, parent, start, end in sections:
            sname = f"{fid}#{n}"
            # the outline is DECLARED structure (T1) — no confidence attr
            sattrs = {"source": "docs", "level": lvl,
                      "page_start": start, "page_end": end}
            stext = "" if needs_ocr else span_text(start, end)
            if stext:
                sattrs["text"] = stext
            nodes.append(node(f"docsection/{sname}", "docsection", title, **sattrs))
            if parent:
                edges.append(raw_edge(f"docsection/{sname}", "child-of",
                                      "docsection", f"{fid}#{parent}"))
            else:
                edges.append(raw_edge(did, "contains", "docsection", sname))
        return nodes, edges


EXTRACTORS = [PdfExtractor()]
