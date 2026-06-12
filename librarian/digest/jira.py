"""Jira digest — graph-builder-backed adapter for collector dumps.

Input is a local dump directory produced by the read-only collector
(``graphbuilder/jira/collect.py``, run on the USER's machine — §7 of
MASTER_PROMPT.md): one ``<dump_dir>/<PROJECT>/<KEY>.issue.json`` file per issue.
This module is strictly read-only over that directory — no network code, ever.

The §14.1 ``parse → to_kus → ingest`` contract, same as mule/graphbuilder:

  * one **raw KU per issue** (``jira:<PROJECT>/<KEY>``) whose body is the raw
    dump JSON verbatim (full issue detail stays readable via ``lib.read_body``,
    exactly how the SF digest stores source files);
  * one **structured graph KU** (``jira:graph/jira``) holding the engine's
    intra-Jira graph serialized via ``persistence.to_json(redact_text=True)`` —
    issue description text NEVER appears inline in the graph JSON (bodies live
    in the raw KUs; redacted nodes carry ``text_redacted``);
  * ``unresolved`` / ``errors`` from the build are carried on the returned
    :class:`JiraDigest` — surfaced, not silently dropped.

Containment rules (owner decisions, 2026-06-12):

  * ``entities`` carry the STRUCTURED issue key ONLY — never summaries, titles
    or any prose-derived name. Jira prose must not pollute the entity bridge;
    the agent finds prose references via full-text search instead.
  * No imports from ``graphbuilder.jira.join`` and no ``documents`` edges —
    Jira is its own contained domain; cross-source wiring is out of scope here.
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

GRAPH_ID = "jira:graph/jira"
GRAPH_PATH = "kb/structured/jira/graph.json"


# --------------------------------------------------------------------------- #
# digest result
# --------------------------------------------------------------------------- #
@dataclass
class JiraIssue:
    """One collected issue — the envelope fields the KU needs, plus the raw
    dump JSON (``source``, the KU body)."""
    key: str
    project_key: str
    title: str = ""                 # the issue summary
    issue_type: str = ""
    status: str = ""
    updated: str = ""
    rel: str = ""                   # dump file path relative to the dump dir
    source: str = ""                # raw dump JSON, verbatim


@dataclass
class JiraDigest:
    issues: list = field(default_factory=list)   # JiraIssue (one per dump file)
    graph: dict = field(default_factory=dict)    # engine {nodes, edges, unresolved, errors}
    unresolved: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    skipped: list = field(default_factory=list)  # files an extractor raised on

    def summary(self) -> dict:
        g = self.graph
        return {
            "issues": len(self.issues),
            "projects": len({i.project_key for i in self.issues}),
            "nodes": len(g.get("nodes", [])),
            "edges": len(g.get("edges", [])),
            "unresolved": len(self.unresolved),
            "errors": len(self.errors),
        }


# --------------------------------------------------------------------------- #
# parse  (dump dir -> engine graph + per-issue records)
# --------------------------------------------------------------------------- #
def _jira_extractors():
    """The Jira extractor only — keeps the build strictly Jira-sourced even if
    foreign files share the tree (a ``*.page.json`` never becomes an issue)."""
    return [e for e in _gb_all_extractors() if getattr(e, "source", None) == "jira"]


def parse_jira(dump_dir, progress=None) -> JiraDigest:
    """Parse a Jira collector dump into a :class:`JiraDigest` (pure; no Librarian).

    Single extraction pass via the engine's two-phase API: each ``*.issue.json``
    is extracted once; the per-file issue nodes feed the raw-KU records and the
    SAME results are resolved into the contained intra-Jira graph. A file the
    extractor raises on lands in ``errors``/``skipped`` — surfaced, not dropped.

    ``progress`` (callable, e.g. ``print``): one-line count every 200 files —
    the extraction loop dominates a big-dump digest (MASTER_PROMPT §4)."""
    root = Path(dump_dir)
    builder = (_GraphBuilder().register(*_jira_extractors())
               .register_resolver(*_gb_default_resolvers()))
    paths = sorted(p for p in root.rglob("*") if p.is_file())
    extracted, errors = _extract_in_chunks(builder, paths, root, progress, "jira parse")

    issues: list = []
    for path, nodes, _raw_edges in extracted:
        n = next((x for x in nodes if x.get("type") == "jiraissue"), None)
        if n is None:                                  # degenerate dump file
            continue
        key = n["id"].split("/", 1)[-1]
        project = n.get("project_key") or path.parent.name
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:                             # pragma: no cover
            rel = path.name
        issues.append(JiraIssue(
            key=key, project_key=project, title=n.get("label") or key,
            issue_type=n.get("issue_type", ""), status=n.get("status", ""),
            updated=n.get("updated", ""), rel=rel,
            source=path.read_text("utf-8", errors="replace"),
        ))

    graph = builder.resolve_extracted(extracted, errors)
    skipped = [f"{e['path']}: {e['error']}" for e in graph["errors"]]
    return JiraDigest(issues=issues, graph=graph,
                      unresolved=graph.get("unresolved", []),
                      errors=graph.get("errors", []), skipped=skipped)


def to_kus(d: JiraDigest):
    """One raw KU per issue (entities = the issue key ONLY — the structured-id
    rule), plus the structured graph KU with inline description text redacted."""
    for rec in d.issues:
        yield KnowledgeUnit(
            id=f"jira:{rec.project_key}/{rec.key}",
            kind="source-record", tier="raw", source="jira",
            path=f"kb/raw/jira/{rec.project_key}/{rec.key}.json",
            title=rec.title,
            entities=[rec.key],
            confidence="VERIFIED",
            provenance={
                "project_key": rec.project_key,
                "issue_type": rec.issue_type,
                "status": rec.status,
                "updated": rec.updated,
                "source_path": rec.rel,
            },
        ), rec.source

    yield KnowledgeUnit(
        id=GRAPH_ID, kind="graph", tier="structured", source="jira",
        path=GRAPH_PATH, title="Jira issue graph", confidence="VERIFIED",
    ), _gb_persistence.to_json(d.graph, redact_text=True)


def ingest_jira(lib, dump_dir, author, rationale, progress=None):
    """Parse a Jira collector dump and commit it through the Librarian. Returns
    ``(Report, JiraDigest)``. Re-ingesting unchanged dumps is a no-op (I9).
    ``progress=print`` narrates every 200 files/KUs (MASTER_PROMPT §4)."""
    d = parse_jira(dump_dir, progress=progress)
    txn = lib.begin(author, rationale)
    for staged, (ku, body) in enumerate(to_kus(d), 1):
        txn.ingest_ku(ku, body=body)
        _tick(progress, "jira ingest", staged)
    return txn.commit(), d


# --------------------------------------------------------------------------- #
# runtime graph access — same convention as the sf/mule digests
# --------------------------------------------------------------------------- #
def load_graph(lib) -> dict:
    body = lib.read_body(GRAPH_ID)
    if not body:
        return {"nodes": [], "edges": [], "unresolved": [], "errors": []}
    return _gb_persistence.from_json(body)
