"""MuleSoft digest — graph-builder-backed adapter.

This replaces the hand-rolled ``xml.etree`` parser with the vendored
**graph-builder** engine (``vendor/graphbuilder/``) — the same migration
``digest/graphbuilder.py`` already did for Salesforce. The Mule extractor lives in
the engine (``graphbuilder/extractors/mule.py`` + ``graphbuilder/mulesoft.py``);
this module is the thin adapter that turns the engine's native
``{nodes, edges, unresolved, errors}`` graph into Knowledge Units and serves the
runtime flow-graph queries.

The §14.1 ``parse → to_kus → ingest`` contract, unchanged:

  * one **raw KU per Mule config file** (``mule:<rel>``), entities = the file's
    flow names + the connector namespaces its flows use, links = ``references`` to
    the other ``mule:`` files its ``<flow-ref>`` targets are defined in;
  * one **structured graph KU** (``mule:graph/mule``) holding the engine graph
    serialized via ``persistence.to_json`` (deterministic; carries a version key);
  * ``unresolved`` / ``errors`` from the build are carried on the returned
    :class:`MuleDigest` — surfaced, not silently dropped (the hand-rolled parser
    buried undefined ``<flow-ref>`` targets as stubs with no diagnostic).

Graph vocabulary is **frozen** for back-compat: ``muleflow/<name>`` /
``muleconnector/<name>`` node ids and ``calls`` (``<flow-ref>``) / ``uses``
(connector) edges — so the query helpers below (``who_calls`` / ``calls_from`` /
``connectors_used`` / ``flows_using`` / ``search_flows``) keep their old names and
return shapes. Sub-flows stay ``muleflow`` nodes with ``kind="sub-flow"``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..schema import KnowledgeUnit

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
from graphbuilder.mulesoft import is_config_path as _is_config_path
from graphbuilder.mulesoft import parse_config as _parse_config
from graphbuilder.mulesoft import rel_path as _rel_path
from graphbuilder.resolvers import default_resolvers as _gb_default_resolvers

GRAPH_ID = "mule:graph/mule"
GRAPH_PATH = "kb/structured/mule/graph.json"


# --------------------------------------------------------------------------- #
# digest result (back-compat shape: .flows / .files, plus engine diagnostics)
# --------------------------------------------------------------------------- #
@dataclass
class MuleFile:
    rel: str
    source: str = ""


@dataclass
class MuleDigest:
    files: list = field(default_factory=list)     # MuleFile (one per Mule config file)
    flows: list = field(default_factory=list)     # graphbuilder.mulesoft.MuleFlow
    graph: dict = field(default_factory=dict)     # engine {nodes, edges, unresolved, errors}
    unresolved: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    def summary(self) -> dict:
        g = self.graph
        return {
            "files": len(self.files),
            "flows": len(self.flows),
            "nodes": len(g.get("nodes", [])),
            "edges": len(g.get("edges", [])),
            "unresolved": len(self.unresolved),
            "errors": len(self.errors),
        }


# --------------------------------------------------------------------------- #
# parse  (Mule app tree -> engine graph + per-file flow records)
# --------------------------------------------------------------------------- #
def _mule_extractors():
    """The Mule extractors only — a Salesforce/Confluence/Jira file never matches
    a Mule config and vice versa, but filtering keeps the build strictly Mule."""
    return [e for e in _gb_all_extractors() if getattr(e, "source", None) == "mule"]


def build_graph(mule_dir) -> dict:
    """The engine's native Mule flow graph for an app tree:
    ``{nodes, edges, unresolved, errors}``. Pure; no Librarian."""
    return (_GraphBuilder().register(*_mule_extractors())
            .register_resolver(*_gb_default_resolvers())
            .build(str(mule_dir)))


def parse_mule(mule_dir) -> MuleDigest:
    """Parse a Mule app tree into a :class:`MuleDigest` (pure; no Librarian).

    Walks every ``src/main/mule`` (or legacy ``src/main/app``) config file, keeps
    its flow records, and builds the engine graph. A non-Mule XML under the config
    root contributes no flows (the engine's root-tag check), so it is not a file."""
    root = Path(mule_dir)
    files: list = []
    flows: list = []
    for p in sorted(q for q in root.rglob("*.xml") if _is_config_path(q)):
        parsed = _parse_config(p)
        if not parsed:
            continue
        files.append(MuleFile(rel=_rel_path(p), source=p.read_text("utf-8", errors="replace")))
        flows.extend(parsed)
    graph = build_graph(root)
    return MuleDigest(
        files=files, flows=flows, graph=graph,
        unresolved=graph.get("unresolved", []), errors=graph.get("errors", []),
    )


def _refs_links(targets):
    return [{"kind": "references", "to": t} for t in targets]


def to_kus(d: MuleDigest):
    """One KU per Mule config file (entities = flow names + connectors; cross-file
    ``<flow-ref>`` -> file→file ``references`` links), plus the structured graph KU.

    KU ids/paths are byte-identical to the hand-rolled digest so re-ingesting
    unchanged sources is an I9 content_hash no-op, not a replace."""
    flow_to_file = {f.name: f.file for f in d.flows}
    flows_by_file: dict = {}
    refs_by_file: dict = {}
    conns_by_file: dict = {}
    for f in d.flows:
        flows_by_file.setdefault(f.file, []).append(f.name)
        conns_by_file.setdefault(f.file, set()).update(f.connectors)
        for r in f.refs:
            tgt = flow_to_file.get(r)
            if tgt and tgt != f.file:
                refs_by_file.setdefault(f.file, set()).add(tgt)

    for mf in d.files:
        ents = list(flows_by_file.get(mf.rel, [])) + sorted(conns_by_file.get(mf.rel, set()))
        links = _refs_links(sorted(f"mule:{t}" for t in refs_by_file.get(mf.rel, set())))
        yield KnowledgeUnit(
            id=f"mule:{mf.rel}", kind="source-record", tier="raw", source="mule",
            path=f"kb/raw/mule/{mf.rel}", title=mf.rel, entities=ents,
            links=links, confidence="VERIFIED",
        ), mf.source

    yield KnowledgeUnit(
        id=GRAPH_ID, kind="graph", tier="structured", source="mule",
        path=GRAPH_PATH, title="Mule flow graph", confidence="VERIFIED",
    ), _gb_persistence.to_json(d.graph)


def ingest_mule(lib, mule_dir, author, rationale):
    """Parse a Mule app and commit it through the Librarian. Returns
    ``(Report, MuleDigest)``. Re-ingesting unchanged content is a no-op (I9)."""
    d = parse_mule(mule_dir)
    txn = lib.begin(author, rationale)
    for ku, body in to_kus(d):
        txn.ingest_ku(ku, body=body)
    return txn.commit(), d


# --------------------------------------------------------------------------- #
# runtime graph queries — same names/returns as the hand-rolled digest. See
# §4.1 of MASTER_PROMPT.md (imported there as ``mule``).
# --------------------------------------------------------------------------- #
def load_graph(lib) -> dict:
    body = lib.read_body(GRAPH_ID)
    if not body:
        return {"nodes": [], "edges": [], "unresolved": [], "errors": []}
    return _gb_persistence.from_json(body)


def flow(graph, name):
    return next((n for n in graph["nodes"] if n["id"] == f"muleflow/{name}"), None)


def who_calls(graph, name) -> list:
    fid = f"muleflow/{name}"
    return [e["src"].split("/", 1)[1] for e in graph["edges"]
            if e["type"] == "calls" and e["dst"] == fid]


def calls_from(graph, name) -> list:
    fid = f"muleflow/{name}"
    return [e["dst"].split("/", 1)[1] for e in graph["edges"]
            if e["type"] == "calls" and e["src"] == fid]


def connectors_used(graph, name) -> list:
    fid = f"muleflow/{name}"
    return [e["dst"].split("/", 1)[1] for e in graph["edges"]
            if e["type"] == "uses" and e["src"] == fid]


def flows_using(graph, connector) -> list:
    cid = f"muleconnector/{connector}"
    return [e["src"].split("/", 1)[1] for e in graph["edges"]
            if e["type"] == "uses" and e["dst"] == cid]


def search_flows(graph, keyword) -> list:
    kw = keyword.lower()
    return [n["label"] for n in graph["nodes"]
            if n["type"] == "muleflow" and kw in n["label"].lower()]
