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

Phase 3 (engine pin 4a59b97) is purely ADDITIVE on top of that freeze: the
engine's richer taxonomy (APIkit surface, source triggers, global configs,
property keys — never values — and build metadata) flows through this adapter as

  * raw KUs for the *support files* (``mule:resources/<rel>`` for RAML specs and
    property files, ``mule:pom.xml`` / ``mule:mule-artifact.json``), with parsed
    names (resource paths, property keys, dependency coordinates) as entities;
  * the Phase-3 query helpers at the bottom (``flow_for_resource`` /
    ``flows_exposed_on`` / ``entrypoints`` / ``flows_reading`` / ``api_resources``
    / ``routes_of`` / ``configs_used`` / ``secure_keys`` / ``app_dependencies``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..schema import KnowledgeUnit
from . import _graphmerge

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
from graphbuilder.mulesoft import is_resources_path as _is_resources_path
from graphbuilder.mulesoft import parse_artifacts as _parse_artifacts
from graphbuilder.mulesoft import parse_config as _parse_config
from graphbuilder.mulesoft import rel_path as _rel_path
from graphbuilder.mulesoft import resource_rel_path as _resource_rel_path
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
    entities: list = field(default_factory=list)  # searchable names (support files)


@dataclass
class MuleDigest:
    files: list = field(default_factory=list)     # MuleFile (one per Mule config file)
    flows: list = field(default_factory=list)     # graphbuilder.mulesoft.MuleFlow
    # Phase-3 support files (RAML specs, property files, pom, descriptor) — kept
    # apart from `files` so the config-file KU contract stays byte-identical.
    support_files: list = field(default_factory=list)  # MuleFile
    graph: dict = field(default_factory=dict)     # engine {nodes, edges, unresolved, errors}
    unresolved: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    def summary(self) -> dict:
        g = self.graph
        return {
            "files": len(self.files),
            "support_files": len(self.support_files),
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


def _support_rel(path: Path) -> str:
    """KU-id tail for a Phase-3 support file: ``resources/<rel>`` for anything
    under ``src/main/resources`` (RAML, property files), the bare name for the
    app-root files (``pom.xml`` / ``mule-artifact.json``). Never collides with a
    config-file rel — support files are never ``.xml`` under ``src/main/mule``."""
    if _is_resources_path(path):
        return f"resources/{_resource_rel_path(path)}"
    return path.name


def parse_mule(mule_dir) -> MuleDigest:
    """Parse a Mule app tree into a :class:`MuleDigest` (pure; no Librarian).

    Walks every ``src/main/mule`` (or legacy ``src/main/app``) config file, keeps
    its flow records, and builds the engine graph. A non-Mule XML under the config
    root contributes no flows (the engine's root-tag check), so it is not a file.

    Phase 3: the support files the engine also parses — RAML specs, property
    files, ``pom.xml``, ``mule-artifact.json`` — land in ``support_files``, each
    carrying the names its extractor found (resource paths, property KEYS, …) as
    ``entities`` for the cross-source bridge. A pom outside a Mule app root (or a
    fragment-only RAML) extracts to nothing and is skipped, mirroring the build."""
    root = Path(mule_dir)
    # config files take the first branch below, so the config extractor never
    # matches in the support pass — no need to filter it out
    extractors = _mule_extractors()
    files: list = []
    flows: list = []
    support: list = []
    for p in sorted(q for q in root.rglob("*") if q.is_file()):
        if _is_config_path(p):
            parsed = _parse_config(p)
            arts = _parse_artifacts(p)
            # Phase 3: a config-only file (global configs / property loads, no
            # flows) is a file too — its declarations are what you retrieve it
            # for. A non-Mule or empty XML still contributes nothing.
            names = sorted({a.name for a in arts.apikit_configs}
                           | {g.name for g in arts.globals})
            if not parsed and not names and not arts.property_files:
                continue
            files.append(MuleFile(rel=_rel_path(p),
                                  source=p.read_text("utf-8", errors="replace"),
                                  entities=names))
            flows.extend(parsed)
            continue
        ex = next((e for e in extractors if e.handles(p)), None)
        if ex is None:
            continue
        try:
            nodes, _ = ex.extract(p)
        except Exception:                      # surfaced via the build's errors
            nodes = []
        if not nodes:
            continue
        entities = sorted({n["label"] for n in nodes if n.get("label")})
        support.append(MuleFile(rel=_support_rel(p),
                                source=p.read_text("utf-8", errors="replace"),
                                entities=entities))
    graph = build_graph(root)
    return MuleDigest(
        files=files, flows=flows, support_files=support, graph=graph,
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

    # Phase-3 support files: one raw KU each (RAML spec, property file, pom,
    # descriptor) — additive, so re-ingesting a pre-Phase-3 app stays an I9 no-op.
    for sf in d.support_files:
        yield KnowledgeUnit(
            id=f"mule:{sf.rel}", kind="source-record", tier="raw", source="mule",
            path=f"kb/raw/mule/{sf.rel}", title=sf.rel, entities=sf.entities,
            confidence="VERIFIED",
        ), sf.source

    yield KnowledgeUnit(
        id=GRAPH_ID, kind="graph", tier="structured", source="mule",
        path=GRAPH_PATH, title="Mule flow graph", confidence="VERIFIED",
    ), _gb_persistence.to_json(d.graph)


def ingest_mule(lib, mule_dir, author, rationale):
    """Parse a Mule app and commit it through the Librarian. Returns
    ``(Report, MuleDigest)``. Re-ingesting unchanged content is a no-op (I9)."""
    d = parse_mule(mule_dir)
    txn = lib.begin(author, rationale)
    existing = _graphmerge.load_existing(lib, GRAPH_ID, _gb_persistence)
    for ku, body in to_kus(d):
        if ku.id == GRAPH_ID:              # accumulate, never replace (see _graphmerge)
            merged = _graphmerge.merge_graphs(existing, _gb_persistence.from_json(body))
            body = _gb_persistence.to_json(merged)
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


# --------------------------------------------------------------------------- #
# Phase-3 queries — the API surface / source triggers / properties / build
# metadata the taxonomy added. Same conventions as above: name lists in graph
# order, exact matching, nothing guessed.
# --------------------------------------------------------------------------- #
def _flow_nodes(graph):
    return [n for n in graph["nodes"] if n["type"] == "muleflow"]


def flow_for_resource(graph, method, path) -> list:
    """Flows that implement an API operation — ``("get", "/orders")`` -> the
    APIkit-convention flows whose decoded name matches. URI params use the RAML
    form (``/orders/{orderId}``)."""
    m = method.lower()
    p = path if path.startswith("/") else f"/{path}"
    return [n["label"] for n in _flow_nodes(graph)
            if n.get("api_method") == m and n.get("api_path") == p]


def flows_exposed_on(graph, path=None) -> list:
    """HTTP-exposed flows as ``[{"flow", "path", "config"}]`` — every flow whose
    source is an HTTP listener, optionally filtered to one exact listener path
    (wildcards like ``/api/*`` are matched literally, not expanded)."""
    out = []
    for n in _flow_nodes(graph):
        if n.get("source_kind") != "httplistener":
            continue
        if path is not None and n.get("source_path") != path:
            continue
        out.append({"flow": n["label"], "path": n.get("source_path", ""),
                    "config": n.get("source_config", "")})
    return out


def entrypoints(graph) -> list:
    """Every externally-triggered flow: ``[{"flow", "kind", "detail"}]`` where
    kind is ``httplistener`` (detail = path), ``scheduler`` (detail = frequency
    or cron, read off the scheduler node) or ``source`` (detail = connector)."""
    sched = {n["label"]: n for n in graph["nodes"] if n["type"] == "scheduler"}
    out = []
    for n in _flow_nodes(graph):
        kind = n.get("source_kind")
        if not kind:
            continue
        if kind == "httplistener":
            detail = n.get("source_path", "")
        elif kind == "scheduler":
            s = sched.get(n["label"], {})
            detail = s.get("frequency") or s.get("cron") or ""
        else:
            detail = next((e["dst"].split("/", 1)[1] for e in graph["edges"]
                           if e["type"] == "triggeredby"
                           and e["src"] == f"muleflow/{n['label']}"), "")
        out.append({"flow": n["label"], "kind": kind, "detail": detail})
    return out


def flows_reading(graph, key) -> list:
    """Flows that read a configuration property (``${key}``)."""
    kid = f"propertykey/{key}"
    return [e["src"].split("/", 1)[1] for e in graph["edges"]
            if e["type"] == "reads" and e["dst"] == kid
            and e["src"].startswith("muleflow/")]


def keys_read_by(graph, flow) -> list:
    """Property keys a flow reads."""
    fid = f"muleflow/{flow}"
    return [e["dst"].split("/", 1)[1] for e in graph["edges"]
            if e["type"] == "reads" and e["src"] == fid]


def api_resources(graph) -> list:
    """The declared API surface: ``[{"path", "methods", "spec"}]``."""
    return [{"path": n["label"], "methods": n.get("methods", []),
             "spec": n.get("spec", "")}
            for n in graph["nodes"] if n["type"] == "apiresource"]


def routes_of(graph, config) -> list:
    """Flows an APIkit router routes to, by its config name."""
    rid = f"apikitrouter/{config}"
    return [e["dst"].split("/", 1)[1] for e in graph["edges"]
            if e["type"] == "routesto" and e["src"] == rid]


def configs_used(graph, flow) -> list:
    """Global / APIkit config names a flow references (``config-ref``)."""
    fid = f"muleflow/{flow}"
    return [e["dst"].split("/", 1)[1] for e in graph["edges"]
            if e["type"] == "usesconfig" and e["src"] == fid]


def secure_keys(graph) -> list:
    """Property keys flagged secret-bearing (descriptor ``secureProperties``,
    ``secure::`` reads) — KEY NAMES only; values are never in the graph."""
    return sorted(n["label"] for n in graph["nodes"]
                  if n["type"] == "propertykey" and n.get("secure"))


def app_dependencies(graph) -> list:
    """The app's pom dependencies (``groupId:artifactId``)."""
    return sorted(n["label"] for n in graph["nodes"] if n["type"] == "pomdependency")
