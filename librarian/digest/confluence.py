"""Confluence digest — graph-builder-backed adapter for collector dumps.

Input is a local dump directory produced by the read-only collector
(``graphbuilder/confluence/collect.py``, run on the USER's machine — §7 of
MASTER_PROMPT.md): one ``<dump_dir>/<SPACE>/<id>.page.json`` file per content
unit (pages and blog posts share the shape). This module is strictly read-only
over that directory — no network code, ever.

The §14.1 ``parse → to_kus → ingest`` contract, same as jira/mule/graphbuilder:

  * one **raw KU per page** (``confluence:<SPACE>/<PAGE-ID>``) whose body is the
    raw dump JSON verbatim (the full page detail — storage-format body included —
    stays readable via ``lib.read_body``, exactly how the SF digest stores
    source files);
  * one **structured graph KU** (``confluence:graph/confluence``) holding the
    engine's intra-Confluence graph serialized via
    ``persistence.to_json(redact_text=True)`` — page body text NEVER appears
    inline in the graph JSON (bodies live in the raw KUs; redacted nodes carry
    ``text_redacted``);
  * ``unresolved`` / ``errors`` from the build are carried on the returned
    :class:`ConfluenceDigest` — surfaced, not silently dropped.

Containment rules (owner decisions, 2026-06-12):

  * ``entities`` carry the STRUCTURED space key + page id ONLY — never titles
    or any prose-derived name. Confluence prose must not pollute the entity
    bridge; the agent finds prose references via full-text search instead.
  * No imports from ``graphbuilder.confluence.join`` /
    ``graphbuilder.confluence.classify`` and no ``documents`` edges —
    Confluence is its own contained domain; cross-source wiring is out of
    scope here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..schema import KnowledgeUnit
from . import _graphmerge
from ._progress import done as _done
from ._progress import extract_in_chunks as _extract_in_chunks
from ._progress import tick as _tick

# --------------------------------------------------------------------------- #
# vendored engine import — works both in the dev repo (package under vendor/)
# and at runtime inside memory.zip (package unpacked at the ZIP root, which
# bootstrap.boot() puts on sys.path). Mirrors digest/graphbuilder.py.
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

GRAPH_ID = "confluence:graph/confluence"
GRAPH_PATH = "kb/structured/confluence/graph.json"


# --------------------------------------------------------------------------- #
# digest result
# --------------------------------------------------------------------------- #
@dataclass
class ConfluencePage:
    """One collected content unit — the envelope fields the KU needs, plus the
    raw dump JSON (``source``, the KU body)."""
    page_id: str
    space_key: str
    title: str = ""
    version: int = 0
    content_type: str = "page"      # "page" | "blogpost"
    rel: str = ""                   # dump file path relative to the dump dir
    source: str = ""                # raw dump JSON, verbatim


@dataclass
class ConfluenceDigest:
    pages: list = field(default_factory=list)    # ConfluencePage (one per dump file)
    graph: dict = field(default_factory=dict)    # engine {nodes, edges, unresolved, errors}
    unresolved: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    skipped: list = field(default_factory=list)  # files an extractor raised on

    def summary(self) -> dict:
        g = self.graph
        return {
            "pages": len(self.pages),
            "spaces": len({p.space_key for p in self.pages}),
            "nodes": len(g.get("nodes", [])),
            "edges": len(g.get("edges", [])),
            "unresolved": len(self.unresolved),
            "errors": len(self.errors),
        }


# --------------------------------------------------------------------------- #
# parse  (dump dir -> engine graph + per-page records)
# --------------------------------------------------------------------------- #
def _confluence_extractors():
    """The Confluence extractor only — keeps the build strictly Confluence-
    sourced even if foreign files share the tree (a ``*.issue.json`` never
    becomes a page)."""
    return [e for e in _gb_all_extractors()
            if getattr(e, "source", None) == "confluence"]


def parse_confluence(dump_dir, progress=None) -> ConfluenceDigest:
    """Parse a Confluence collector dump into a :class:`ConfluenceDigest`
    (pure; no Librarian).

    Single extraction pass via the engine's two-phase API: each ``*.page.json``
    is extracted once; the per-file page nodes feed the raw-KU records and the
    SAME results are resolved into the contained intra-Confluence graph. A file
    the extractor raises on lands in ``errors``/``skipped`` — surfaced, not
    dropped.

    ``progress`` (callable, e.g. ``print``): one-line count every ``EVERY``
    files (default 1000) — the extraction loop dominates a big-dump digest
    (MASTER_PROMPT §4)."""
    root = Path(dump_dir)
    builder = (_GraphBuilder().register(*_confluence_extractors())
               .register_resolver(*_gb_default_resolvers()))
    paths = sorted(p for p in root.rglob("*") if p.is_file())
    extracted, errors = _extract_in_chunks(builder, paths, root, progress,
                                           "confluence parse")

    pages: list = []
    for path, nodes, _raw_edges in extracted:
        n = next((x for x in nodes if x.get("type") == "page"), None)
        if n is None:                                  # degenerate dump file
            continue
        # collector layout is <SPACE>/<id>.page.json; the parsed envelope wins,
        # the file name/dir are the fallback for a degenerate dump
        page_id = n.get("page_id") or path.name[:-len(".page.json")]
        space = n.get("space_key") or path.parent.name
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:                             # pragma: no cover
            rel = path.name
        pages.append(ConfluencePage(
            page_id=str(page_id), space_key=str(space),
            title=n.get("label") or str(page_id),
            version=n.get("version", 0),
            content_type=n.get("content_type", "page"),
            rel=rel, source=path.read_text("utf-8", errors="replace"),
        ))

    graph = builder.resolve_extracted(extracted, errors)
    skipped = [f"{e['path']}: {e['error']}" for e in graph["errors"]]
    return ConfluenceDigest(pages=pages, graph=graph,
                            unresolved=graph.get("unresolved", []),
                            errors=graph.get("errors", []), skipped=skipped)


def to_kus(d: ConfluenceDigest):
    """One raw KU per page (entities = space key + page id ONLY — the
    structured-id rule), plus the structured graph KU with inline body text
    redacted."""
    for rec in d.pages:
        yield KnowledgeUnit(
            id=f"confluence:{rec.space_key}/{rec.page_id}",
            kind="source-record", tier="raw", source="confluence",
            path=f"kb/raw/confluence/{rec.space_key}/{rec.page_id}.json",
            title=rec.title,
            entities=[rec.space_key, rec.page_id],
            confidence="VERIFIED",
            provenance={
                "space_key": rec.space_key,
                "page_id": rec.page_id,
                "version": rec.version,
                "content_type": rec.content_type,
                "source_path": rec.rel,
            },
        ), rec.source

    yield KnowledgeUnit(
        id=GRAPH_ID, kind="graph", tier="structured", source="confluence",
        path=GRAPH_PATH, title="Confluence page graph", confidence="VERIFIED",
    ), _gb_persistence.to_json(d.graph, redact_text=True)


def ingest_confluence(lib, dump_dir, author, rationale, progress=None, *, dg=None):
    """Parse a Confluence collector dump and commit it through the Librarian.
    Returns ``(Report, ConfluenceDigest)``. Re-ingesting unchanged dumps is a
    no-op (I9). ``progress=print`` narrates every ``EVERY`` files/KUs
    (MASTER_PROMPT §4).

    ``dg`` (keyword-only): pass a pre-parsed :class:`ConfluenceDigest` from a
    preceding ``parse_confluence()`` call to skip the re-parse. ``dump_dir``
    is still required by the signature but is unused for parsing when ``dg``
    is given."""
    if dg is None:
        dg = parse_confluence(dump_dir, progress=progress)
    txn = lib.begin(author, rationale)
    existing = _graphmerge.load_existing(lib, GRAPH_ID, _gb_persistence)
    staged = 0
    for staged, (ku, body) in enumerate(to_kus(dg), 1):
        if ku.id == GRAPH_ID:              # accumulate, never replace (see _graphmerge)
            merged = _graphmerge.merge_graphs(existing, _gb_persistence.from_json(body))
            body = _gb_persistence.to_json(merged)
        txn.ingest_ku(ku, body=body)
        _tick(progress, "confluence ingest", staged)
    _done(progress, "confluence ingest", staged)
    return txn.commit(), dg


# --------------------------------------------------------------------------- #
# runtime graph access — same convention as the sf/mule digests
# --------------------------------------------------------------------------- #
def load_graph(lib) -> dict:
    body = lib.read_body(GRAPH_ID)
    if not body:
        return {"nodes": [], "edges": [], "unresolved": [], "errors": []}
    return _gb_persistence.from_json(body)
