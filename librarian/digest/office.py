"""Office-docs digest — graph-builder-backed adapter for ``.docx`` / ``.xlsx`` /
``.xlsm`` / ``.pptx`` / ``.pptm`` uploads.

Input is a directory of office documents the user uploaded (any layout; the
vendored extractors' ``handles()`` decide what is a document — legacy binary
``.doc`` / ``.xls`` / ``.xlsb`` / ``.ppt`` are rejected by the engine and simply
skipped).
The §14.1 ``parse → to_kus → ingest`` contract, same as jira/mule/graphbuilder,
with one docs-specific twist — THREE artifacts per document:

  * one **raw KU per file** (``docs:<relpath>``) whose body is a **media-stripped
    working copy** of the uploaded file: images and embedded media are removed
    from OOXML zips (every XML part — slides, notes, section text, tables,
    chart XML including cached numeric values — is kept byte-identical, so the
    agent can re-open and re-parse text, tables and chart data on demand from
    the raw KU). PDF files (not an OOXML zip) are stored verbatim. The user
    keeps their own original; the stored copy is the agent's working copy and
    may not reopen cleanly in PowerPoint/Word due to dangling image references.
    The Librarian's body pipeline is bytes-native (``content_hash`` /
    ``Store.write`` / ``lib.read_body`` all take and return ``bytes`` untouched).
  * one **plain-text sidecar KU** (``docs:<relpath>#text``, stored next to the
    file as ``<relpath>.txt``) holding the extracted plaintext: section titles +
    section body text for Word; sheet/table names + column names for Excel;
    slide titles + body text + speaker notes + chart series/category labels for
    PowerPoint. THIS is the document's full-text-search surface — FTS indexes it
    directly, so a prose question hits documents without anyone re-parsing a
    binary. (A parent section's text spans its subsections — see the engine — so
    nested text may repeat in the sidecar; harmless for FTS, and the raw file
    remains the fidelity copy.) A document with no extractable text gets no
    sidecar.
  * one **structured graph KU** (``docs:graph/docs``) — the contained intra-docs
    graph (docfile/docsection/sheet/datatable/slide/chart, ``contains``/
    ``child-of``) serialized via ``persistence.to_json(redact_text=True)``):
    section body text and slide text/notes NEVER appear inline in the graph JSON
    (they live in the sidecars; redacted nodes carry ``text_redacted``).

Containment rules (owner decisions, 2026-06-12 — the prose rule, hardened):

  * ``entities`` are ALWAYS EMPTY — never filenames, titles, headings or column
    names. Document prose must not pollute the entity bridge; the agent finds
    references in documents via full-text search over the sidecars instead.
  * No cross-domain edges and no join/classify imports — ``docs`` is its own
    contained domain. Jira keys / ``X__c`` names detected in document text are
    ATTRS on the graph nodes (engine policy), never edges, never entities.
  * A corrupt or unreadable file never raises: the engine records it in
    ``errors`` and the digest surfaces it (``errors`` / ``skipped``).
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..schema import KnowledgeUnit
from ._progress import done as _done
from ._progress import extract_in_chunks as _extract_in_chunks
from ._progress import tick as _tick

# --------------------------------------------------------------------------- #
# media stripping
# --------------------------------------------------------------------------- #
_DROPPABLE_DIRS = {"media", "embeddings"}
_DROPPABLE_FILE = "docProps/thumbnail."   # any extension


def _strip_media(data: bytes) -> bytes:
    """Return a media-stripped copy of an OOXML zip, or the original bytes
    unchanged for non-OOXML input (e.g. PDF — non-PK magic passthrough).

    Dropped entries: any zip member whose path contains a ``media/`` or
    ``embeddings/`` directory segment (covers ``ppt/media``, ``word/media``,
    ``xl/media``, ``ppt/embeddings``, ``xl/embeddings``) and
    ``docProps/thumbnail.*``.  ALL other entries — every XML part, ``.rels``
    files, ``[Content_Types].xml`` — are kept with byte-identical content.

    If NOTHING is droppable the ORIGINAL ``bytes`` object is returned unchanged
    so media-free documents keep their exact original bytes and existing content
    hashes (I9: re-ingesting the same file stays a no-op).

    Otherwise the zip is rewritten DETERMINISTICALLY: entries in original order,
    fixed ``ZipInfo.date_time`` ``(1980, 1, 1, 0, 0, 0)``, no extra fields or
    comments, ``ZIP_DEFLATED`` compression.  The same input always produces
    identical output bytes (I9: re-ingesting the same deck hashes identically
    and no-ops).

    Storage policy: the stored copy is the AGENT'S working copy — all XML is
    retained (slides/notes/tables/chart XML including cached numeric values stay
    re-parseable from the raw KU), but image references dangle so it is not
    guaranteed to reopen cleanly in PowerPoint/Word; the user keeps their own
    original.
    """
    # Non-OOXML (PDF or unknown): PK magic check — passthrough verbatim.
    if data[:4] != b"PK\x03\x04":
        return data

    try:
        src = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile:
        # Malformed zip — return as-is; the engine will surface the error.
        return data

    def _droppable(name: str) -> bool:
        parts = name.replace("\\", "/").split("/")
        # Any segment is a media/embeddings directory
        for seg in parts[:-1]:
            if seg in _DROPPABLE_DIRS:
                return True
        # docProps/thumbnail.<ext>
        if name.startswith(_DROPPABLE_FILE):
            return True
        return False

    members = src.infolist()
    drops = [m for m in members if _droppable(m.filename)]
    if not drops:
        src.close()
        return data   # no media — return the original object unchanged

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for info in members:
            if _droppable(info.filename):
                continue
            zi = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.comment = b""
            zi.extra = b""
            dst.writestr(zi, src.read(info.filename))
    src.close()
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# vendored engine import — works both in the dev repo (package under vendor/)
# and at runtime inside memory.zip (package unpacked at the ZIP root, which
# bootstrap.boot() puts on sys.path). Mirrors digest/jira.py.
# --------------------------------------------------------------------------- #
try:
    import graphbuilder as _gb  # noqa: F401
except ImportError:  # pragma: no cover - dev-repo path
    import sys
    _vendor = Path(__file__).resolve().parents[2] / "vendor"
    if _vendor.is_dir():
        sys.path.insert(0, str(_vendor))
    import graphbuilder as _gb  # noqa: F401

from graphbuilder import persistence as _gb_persistence
from graphbuilder.core import GraphBuilder as _GraphBuilder
from graphbuilder.extractors import all_extractors as _gb_all_extractors
from graphbuilder.resolvers import default_resolvers as _gb_default_resolvers

GRAPH_ID = "docs:graph/docs"
GRAPH_PATH = "kb/structured/docs/graph.json"


# --------------------------------------------------------------------------- #
# digest result
# --------------------------------------------------------------------------- #
@dataclass
class OfficeDoc:
    """One parsed document — the envelope fields the KUs need, plus the
    media-stripped working copy bytes (``data``, the raw-KU body) and the
    extracted plaintext (``text``, the sidecar body)."""
    rel: str                    # path relative to the upload dir (the KU id segment)
    name: str                   # filename (the KU title — identity, not prose)
    file_id: str = ""           # sha1-12 of the file bytes (= docfile/<id> in the graph)
    doc_type: str = ""          # docx | xlsx (the format family; xlsm reports xlsx)
    structure: str = ""         # declared | heuristic | none (the engine's tier)
    text: str = ""              # extracted plaintext ("" -> no sidecar KU)
    data: bytes = b""           # media-stripped working copy (OOXML) or verbatim (PDF)


@dataclass
class OfficeDigest:
    documents: list = field(default_factory=list)  # OfficeDoc (one per handled file)
    graph: dict = field(default_factory=dict)      # engine {nodes, edges, unresolved, errors}
    unresolved: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    skipped: list = field(default_factory=list)    # files an extractor raised on

    def summary(self) -> dict:
        g = self.graph
        doc_types: dict = {}
        for doc in self.documents:
            doc_types[doc.doc_type] = doc_types.get(doc.doc_type, 0) + 1
        return {
            "documents": len(self.documents),
            "doc_types": doc_types,
            "with_text": sum(1 for doc in self.documents if doc.text),
            "nodes": len(g.get("nodes", [])),
            "edges": len(g.get("edges", [])),
            "unresolved": len(self.unresolved),
            "errors": len(self.errors),
        }


# --------------------------------------------------------------------------- #
# parse  (upload dir -> engine graph + per-document records)
# --------------------------------------------------------------------------- #
def _office_extractors():
    """The office extractors only (``source == "docs"``: docx + xlsx) — keeps
    the build strictly docs-sourced even if foreign files share the tree (a
    ``*.issue.json`` next to the documents never becomes a docfile)."""
    return [e for e in _gb_all_extractors() if getattr(e, "source", None) == "docs"]


def _plaintext(nodes) -> str:
    """The sidecar body, assembled from the extracted nodes in document order:
    Word section titles + body text (the docfile's preamble/flat text first);
    Excel sheet/table names + column names; PowerPoint slide titles + body text
    + speaker notes + chart series/category labels. Names and the engine's
    deliberate text captures only — cell values / formulas / numeric data values
    / authors never reach the extractor output, so they cannot reach the sidecar
    either."""
    lines: list = []
    for n in nodes:
        ntype = n.get("type")
        if ntype == "docfile":
            if n.get("title"):
                lines.append(n["title"])
            if n.get("text"):
                lines.append(n["text"])
            if n.get("defined_names"):
                lines.append("Defined names: " + ", ".join(n["defined_names"]))
            if n.get("columns"):
                lines.append("Columns: " + ", ".join(n["columns"]))
        elif ntype == "docsection":
            lines.append(n.get("label") or "")
            if n.get("text"):
                lines.append(n["text"])
            if n.get("columns"):
                lines.append("Columns: " + ", ".join(n["columns"]))
        elif ntype in ("sheet", "datatable"):
            kind = "Sheet" if ntype == "sheet" else "Table"
            lines.append(f"{kind}: {n.get('label') or ''}")
            if n.get("columns"):
                lines.append("Columns: " + ", ".join(n["columns"]))
        elif ntype == "slide":
            lines.append(f"Slide: {n.get('label') or ''}")
            if n.get("text"):
                lines.append(n["text"])
            if n.get("notes"):
                lines.append(n["notes"])
            if n.get("columns"):
                lines.append("Columns: " + ", ".join(n["columns"]))
        elif ntype == "chart":
            lines.append(f"Chart: {n.get('label') or ''}")
            if n.get("series"):
                lines.append("Series: " + ", ".join(n["series"]))
            if n.get("categories"):
                lines.append("Categories: " + ", ".join(n["categories"]))
    return "\n".join(line for line in lines if line).strip()


def parse_office(docs_dir, progress=None, *, strip_media=True) -> OfficeDigest:
    """Parse a single office document — or a directory of them — into an
    :class:`OfficeDigest` (pure; no Librarian).

    ``docs_dir`` may be a directory (walked recursively) OR a path to a lone
    document (e.g. one ``.pptx``); the single-file form is treated as if it sat
    alone in its parent folder, so ``rel`` paths stay relative to that parent.

    Single extraction pass via the engine's two-phase API: each handled file is
    extracted once; the per-file nodes feed the raw-KU records + sidecar text
    and the SAME results are resolved into the contained intra-docs graph. A
    file the extractor raises on (corrupt zip, malformed XML) lands in
    ``errors``/``skipped`` — surfaced, not dropped; files no office extractor
    handles are simply not documents.

    ``progress`` (callable, e.g. ``print``): one-line count every ``EVERY``
    files (default 1000) — the extraction loop dominates a big-upload digest
    (MASTER_PROMPT §4).

    ``strip_media`` (keyword-only, default ``True``): remove embedded images and
    media from OOXML zips before storing as the raw KU body.  Set to ``False``
    to retain the original bytes verbatim (e.g. for offline diff/audit)."""
    root = Path(docs_dir)
    builder = (_GraphBuilder().register(*_office_extractors())
               .register_resolver(*_gb_default_resolvers()))
    if root.is_file():               # accept a lone document, not just a folder
        paths, root = [root], root.parent
    else:
        paths = sorted(p for p in root.rglob("*") if p.is_file())
    extracted, errors = _extract_in_chunks(builder, paths, root, progress,
                                           "office parse")

    documents: list = []
    for path, nodes, _raw_edges in extracted:
        n = next((x for x in nodes if x.get("type") == "docfile"), None)
        if n is None:                                  # degenerate extraction
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:                             # pragma: no cover
            rel = path.name
        raw = path.read_bytes()
        body = _strip_media(raw) if strip_media else raw
        documents.append(OfficeDoc(
            rel=rel, name=path.name,
            file_id=n["id"].split("/", 1)[-1],
            doc_type=n.get("doc_type", ""),
            structure=n.get("structure", ""),
            text=_plaintext(nodes),
            data=body,
        ))

    graph = builder.resolve_extracted(extracted, errors)
    skipped = [f"{e['path']}: {e['error']}" for e in graph["errors"]]
    return OfficeDigest(documents=documents, graph=graph,
                        unresolved=graph.get("unresolved", []),
                        errors=graph.get("errors", []), skipped=skipped)


def to_kus(d: OfficeDigest):
    """Per document: the raw KU (media-stripped working copy for OOXML, or
    verbatim bytes for PDF — decided at parse time by ``parse_office``'s
    ``strip_media``) + the plain-text sidecar (skipped when there is no text),
    then the structured graph KU with all inline section text redacted.
    ``entities`` are ALWAYS empty — the prose rule; documents are found via FTS
    over the sidecars, never the bridge."""
    for rec in d.documents:
        raw_id = f"docs:{rec.rel}"
        prov = {
            "doc_type": rec.doc_type,
            "sha12": rec.file_id,
            "structure": rec.structure,
            "source_path": rec.rel,
        }
        yield KnowledgeUnit(
            id=raw_id, kind="source-record", tier="raw", source="docs",
            path=f"kb/raw/docs/{rec.rel}",
            title=rec.name,
            entities=[],                       # ALWAYS — never titles/headers
            confidence="VERIFIED",
            provenance=dict(prov),
        ), rec.data                            # the original file, verbatim

        if rec.text:                           # no text -> no sidecar
            yield KnowledgeUnit(
                id=f"{raw_id}#text", kind="source-record", tier="raw", source="docs",
                path=f"kb/raw/docs/{rec.rel}.txt",
                title=f"{rec.name} (extracted text)",
                entities=[],                   # ALWAYS
                links=[{"kind": "derived-from", "to": raw_id}],
                confidence="VERIFIED",
                provenance=dict(prov),
            ), rec.text

    yield KnowledgeUnit(
        id=GRAPH_ID, kind="graph", tier="structured", source="docs",
        path=GRAPH_PATH, title="Office document graph", confidence="VERIFIED",
    ), _gb_persistence.to_json(d.graph, redact_text=True)


def ingest_office(lib, docs_dir, author, rationale, progress=None, *,
                  dg=None, strip_media=True):
    """Parse a single office document — or a directory of them — and commit it
    through the Librarian (``docs_dir`` accepts either; see :func:`parse_office`).
    Returns ``(Report, OfficeDigest)``. Re-ingesting unchanged
    documents is a no-op (I9 — the stored body's content hash drives it).
    ``progress=print`` narrates every ``EVERY`` files/KUs (MASTER_PROMPT §4).

    ``dg`` (keyword-only): pass a pre-parsed :class:`OfficeDigest` from a
    preceding ``parse_office()`` call to skip the re-parse. ``docs_dir`` is
    still required by the signature but is unused for parsing when ``dg``
    is given.

    ``strip_media`` (keyword-only, default ``True``): remove embedded images and
    media from OOXML zips before storing the raw KU body.  Pass ``False`` to
    retain original bytes (ignored when ``dg`` is supplied — stripping is
    applied at parse time)."""
    if dg is None:
        dg = parse_office(docs_dir, progress=progress, strip_media=strip_media)
    txn = lib.begin(author, rationale)
    staged = 0
    for staged, (ku, body) in enumerate(to_kus(dg), 1):
        txn.ingest_ku(ku, body=body)
        _tick(progress, "office ingest", staged)
    _done(progress, "office ingest", staged)
    return txn.commit(), dg


# --------------------------------------------------------------------------- #
# runtime graph access — same convention as the sf/mule/jira digests
# --------------------------------------------------------------------------- #
def load_graph(lib) -> dict:
    body = lib.read_body(GRAPH_ID)
    if not body:
        return {"nodes": [], "edges": [], "unresolved": [], "errors": []}
    return _gb_persistence.from_json(body)
