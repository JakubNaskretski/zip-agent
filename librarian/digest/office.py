"""Office-docs digest — graph-builder-backed adapter for ``.docx`` / ``.xlsx`` /
``.xlsm`` uploads.

Input is a directory of office documents the user uploaded (any layout; the
vendored extractors' ``handles()`` decide what is a document — legacy binary
``.doc`` / ``.xls`` / ``.xlsb`` are rejected by the engine and simply skipped).
The §14.1 ``parse → to_kus → ingest`` contract, same as jira/mule/graphbuilder,
with one docs-specific twist — THREE artifacts per document:

  * one **raw KU per file** (``docs:<relpath>``) whose body is the ORIGINAL
    FILE BYTES, verbatim. The Librarian's body pipeline is bytes-native
    (``content_hash`` / ``Store.write`` / ``lib.read_body`` all take and return
    ``bytes`` untouched), so the agent can re-open — and re-parse — the exact
    uploaded file on demand; no base64, no lossy decode.
  * one **plain-text sidecar KU** (``docs:<relpath>#text``, stored next to the
    file as ``<relpath>.txt``) holding the extracted plaintext: section titles +
    section body text for Word, sheet/table names + column names for Excel.
    THIS is the document's full-text-search surface — FTS indexes it directly,
    so a prose question hits documents without anyone re-parsing a binary.
    (A parent section's text spans its subsections — see the engine — so nested
    text may repeat in the sidecar; harmless for FTS, and the raw file remains
    the fidelity copy.) A document with no extractable text gets no sidecar.
  * one **structured graph KU** (``docs:graph/docs``) — the contained intra-docs
    graph (docfile/docsection/sheet/datatable, ``contains``/``child-of``)
    serialized via ``persistence.to_json(redact_text=True)``: section body text
    NEVER appears inline in the graph JSON (it lives in the sidecars; redacted
    nodes carry ``text_redacted``).

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

from dataclasses import dataclass, field
from pathlib import Path

from ..schema import KnowledgeUnit
from ._progress import extract_in_chunks as _extract_in_chunks
from ._progress import tick as _tick

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
    original bytes (``data``, the raw-KU body) and the extracted plaintext
    (``text``, the sidecar body)."""
    rel: str                    # path relative to the upload dir (the KU id segment)
    name: str                   # filename (the KU title — identity, not prose)
    file_id: str = ""           # sha1-12 of the file bytes (= docfile/<id> in the graph)
    doc_type: str = ""          # docx | xlsx (the format family; xlsm reports xlsx)
    structure: str = ""         # declared | heuristic | none (the engine's tier)
    text: str = ""              # extracted plaintext ("" -> no sidecar KU)
    data: bytes = b""           # ORIGINAL file bytes, verbatim


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
    Word section titles + body text (the docfile's preamble/flat text first),
    Excel sheet/table names + column names. Names and the engine's deliberate
    text captures only — cell values / formulas / authors never reach the
    extractor output, so they cannot reach the sidecar either."""
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
    return "\n".join(line for line in lines if line).strip()


def parse_office(docs_dir, progress=None) -> OfficeDigest:
    """Parse a directory of office documents into an :class:`OfficeDigest`
    (pure; no Librarian).

    Single extraction pass via the engine's two-phase API: each handled file is
    extracted once; the per-file nodes feed the raw-KU records + sidecar text
    and the SAME results are resolved into the contained intra-docs graph. A
    file the extractor raises on (corrupt zip, malformed XML) lands in
    ``errors``/``skipped`` — surfaced, not dropped; files no office extractor
    handles are simply not documents.

    ``progress`` (callable, e.g. ``print``): one-line count every 200 files —
    the extraction loop dominates a big-upload digest (MASTER_PROMPT §4)."""
    root = Path(docs_dir)
    builder = (_GraphBuilder().register(*_office_extractors())
               .register_resolver(*_gb_default_resolvers()))
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
        documents.append(OfficeDoc(
            rel=rel, name=path.name,
            file_id=n["id"].split("/", 1)[-1],
            doc_type=n.get("doc_type", ""),
            structure=n.get("structure", ""),
            text=_plaintext(nodes),
            data=path.read_bytes(),
        ))

    graph = builder.resolve_extracted(extracted, errors)
    skipped = [f"{e['path']}: {e['error']}" for e in graph["errors"]]
    return OfficeDigest(documents=documents, graph=graph,
                        unresolved=graph.get("unresolved", []),
                        errors=graph.get("errors", []), skipped=skipped)


def to_kus(d: OfficeDigest):
    """Per document: the raw KU (original bytes) + the plain-text sidecar
    (skipped when there is no text), then the structured graph KU with all
    inline section text redacted. ``entities`` are ALWAYS empty — the prose
    rule; documents are found via FTS over the sidecars, never the bridge."""
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


def ingest_office(lib, docs_dir, author, rationale, progress=None):
    """Parse a directory of office documents and commit it through the
    Librarian. Returns ``(Report, OfficeDigest)``. Re-ingesting unchanged
    documents is a no-op (I9 — the file bytes' content hash drives it).
    ``progress=print`` narrates every 200 files/KUs (MASTER_PROMPT §4)."""
    d = parse_office(docs_dir, progress=progress)
    txn = lib.begin(author, rationale)
    for staged, (ku, body) in enumerate(to_kus(d), 1):
        txn.ingest_ku(ku, body=body)
        _tick(progress, "office ingest", staged)
    return txn.commit(), d


# --------------------------------------------------------------------------- #
# runtime graph access — same convention as the sf/mule/jira digests
# --------------------------------------------------------------------------- #
def load_graph(lib) -> dict:
    body = lib.read_body(GRAPH_ID)
    if not body:
        return {"nodes": [], "edges": [], "unresolved": [], "errors": []}
    return _gb_persistence.from_json(body)
