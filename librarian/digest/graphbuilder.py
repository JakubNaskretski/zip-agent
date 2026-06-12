"""Salesforce digest — graph-builder-backed adapter.

This module replaces the hand-rolled ``digest/salesforce.py`` + ``omnistudio.py``
parsers with the vendored **graph-builder** engine (``vendor/graphbuilder/``),
which carries 26 Salesforce extractors (objects, Apex w/ methods, triggers,
flows + flow-elements, LWC, Aura, Visualforce, flexipages, layouts, permission
sets/profiles/groups, sharing rules, approval processes, reports, rules,
OmniStudio, labels, …) — a strict superset of the old digest's coverage.

Vendoring pin (the graph-builder commit this engine was copied from — see
``vendor/README.md``):

    733c202efaab0042b2fd27c83fa9698710f8ffe9   (2026-06-12, main: Phase-3 Mule
    taxonomy + managed-package component refs + two-phase build API +
    schema-aware resolvers suppressing field-token/platform-call noise)

What the adapter does (the §14.1 ``parse → to_kus → ingest`` contract):

  * one **raw KU per source file** (objects, classes, triggers, flows, LWC,
    flexipages, perms, OmniStudio, …); sub-elements (fields, methods, record
    types, flow elements) are graph nodes inside the structured graph, never
    their own manifest entry (KU-granularity rule, ARCH §2.3);
  * one **structured graph KU** (``salesforce:graph/sf``) holding the engine's
    native ``{nodes, edges, unresolved, errors}`` graph verbatim;
  * file names + field/record-type names land in each KU's ``entities`` so the
    cross-source entity bridge joins them automatically;
  * ``unresolved`` / ``errors`` from the build are carried on the returned
    :class:`Digest` (no silent truncation — surface them in the digest report).

Graph vocabulary: the engine's graph JSON shape is the same one zip-agent's
``sf.*`` helpers already consume — ``{"nodes": [{"id": "type/name", "type",
"label", ...}], "edges": [{"src", "dst", "type"}]}`` with ``type/name`` node ids.
The query helpers below (``fields_of`` / ``triggers_on`` / ``who_calls`` / …)
keep their old names and return shapes; only two spots differ from the engine's
native convention and are handled here: ``field_of`` runs field→object (not
object→field) and a field's type lives in the ``field_type`` attr (not ``ftype``).
The engine also emits many more typed edges than the retired digest (``extends``/
``implements``/``reads``/``writes``/``contains``/``invocable``/…), all reachable
through the generic ``neighbors``/``dependents`` helpers.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..schema import KnowledgeUnit

# --------------------------------------------------------------------------- #
# vendored engine import — works both in the dev repo (package under vendor/)
# and at runtime inside memory.zip (package unpacked at the ZIP root, which
# bootstrap.boot() puts on sys.path).
# --------------------------------------------------------------------------- #
try:
    import graphbuilder as _gb  # noqa: F401
except ImportError:  # pragma: no cover - dev-repo path
    import sys
    _vendor = Path(__file__).resolve().parents[2] / "vendor"
    if _vendor.is_dir():
        sys.path.insert(0, str(_vendor))
    import graphbuilder as _gb  # noqa: F401

from graphbuilder import build_graph as _gb_build_graph
from graphbuilder import persistence as _gb_persistence
from graphbuilder.core import GraphBuilder as _GraphBuilder
from graphbuilder.extractors import all_extractors as _gb_all_extractors
from graphbuilder.resolvers import default_resolvers as _gb_default_resolvers

# pin recorded in the built-in tool KU (and echoed in this module's docstring)
_VENDORED_SHA = "24b2b7e"   # engine main — tree-sitter>=0.25 floor, probe docs
_VENDORED_AT = "2026-06-12"

GRAPH_ID = "salesforce:graph/sf"
GRAPH_PATH = "kb/structured/salesforce/graph.json"
TOOL_ID = "agent:tool/graphbuilder"
TOOL_PATH = "tools/graphbuilder/PROVENANCE.json"

# Node kinds that live *inside* a parent KU (graph nodes, never their own KU).
_SUB_KINDS = {"field", "apexmethod", "recordtype", "flowelement"}
# Edge types whose object target is "an object this component deals with" — the
# target object name is added to the source KU's entities so cross_source() lists
# the component under that object (mirrors the old digest putting trigger.sobject,
# flexipage.sobject, flow.objects, permset.objects, omni.object_refs in entities).
_SUBJECT_EDGE_TYPES = {"on", "page-for", "touches", "grants", "maps", "writes", "reads"}


# --------------------------------------------------------------------------- #
# digest result
# --------------------------------------------------------------------------- #
class Digest:
    """Result of :func:`digest` — the native graph plus the KUs to ingest and
    the build-level diagnostics that must not vanish (``unresolved`` / ``errors``)."""

    def __init__(self, graph, kus, skipped):
        self.graph = graph
        self.kus = kus                       # list[(KnowledgeUnit, body)]
        self.unresolved = graph.get("unresolved", [])
        self.errors = graph.get("errors", [])
        self.skipped = skipped               # files an extractor raised on

    def node_type_counts(self) -> dict:
        out: dict = {}
        for n in self.graph.get("nodes", []):
            out[n.get("type", "?")] = out.get(n.get("type", "?"), 0) + 1
        return dict(sorted(out.items()))

    def edge_type_counts(self) -> dict:
        out: dict = {}
        for e in self.graph.get("edges", []):
            out[e.get("type", "?")] = out.get(e.get("type", "?"), 0) + 1
        return dict(sorted(out.items()))

    def summary(self) -> dict:
        g = self.graph
        return {
            "kus": len(self.kus),
            "nodes": len(g.get("nodes", [])),
            "edges": len(g.get("edges", [])),
            "unresolved": len(self.unresolved),
            "errors": len(self.errors),
            "skipped": len(self.skipped),
            "node_types": self.node_type_counts(),
            "edge_types": self.edge_type_counts(),
        }

    def __repr__(self):
        s = self.summary()
        return ("SF digest (graph-builder): "
                f"{s['kus']} KUs, {s['nodes']} nodes, {s['edges']} edges, "
                f"{s['unresolved']} unresolved refs, {s['errors']} extractor errors, "
                f"{s['skipped']} skipped files")


# --------------------------------------------------------------------------- #
# parse  (force-app tree -> graph + per-file raw KUs)
# --------------------------------------------------------------------------- #
def _sf_extractors():
    """The Salesforce extractors only (Confluence/Jira extractors never match a
    force-app file, but filtering keeps the raw-KU pass strictly SF-sourced)."""
    return [e for e in _gb_all_extractors() if getattr(e, "source", None) == "salesforce"]


def build_graph(force_app_dir) -> dict:
    """The engine's native metadata graph for a force-app tree:
    ``{nodes, edges, unresolved, errors}``. Pure; no Librarian."""
    return _gb_build_graph(str(force_app_dir))


def _name_seg(node_id: str) -> str:
    """``object/Account`` -> ``Account``; ``field/Account.Name`` -> ``Account.Name``."""
    return node_id.split("/", 1)[-1]


def _entities_for(nodes) -> list:
    """Searchable anchors for a file's KU: the file's own component names, plus
    bare field / record-type names (so 'where is TotalCost__c used' resolves via
    the entity bridge even though fields are graph nodes, not KUs)."""
    names: list = []
    for n in nodes:
        seg = _name_seg(n["id"])
        kind = n.get("type")
        if kind in ("field", "recordtype"):
            names.append(seg.split(".", 1)[-1])     # bare field / record-type name
        elif kind not in _SUB_KINDS:
            names.append(seg)                        # top-level component name
        # apexmethod / flowelement stay graph-only (avoids generic-name noise)
    return names


def _links_for(raw_edges) -> list:
    """`references` links to the top-level SF KUs this file points at. Sub-element
    targets are reduced to their parent KU (``field/Obj.F`` -> ``object/Obj``,
    ``apexmethod/Cls.m`` -> ``apexclass/Cls``). `references` is exempt from the
    no-orphan check (I6), so off-repo/standard targets are harmless here."""
    links: list = []
    seen: set = set()
    for e in raw_edges:
        tk = e.get("to_kind")
        tn = e.get("to_name")
        if not tk or not tn:
            continue
        if tk == "field" and "." in tn:
            tk, tn = "object", tn.split(".", 1)[0]
        elif tk == "apexmethod" and "." in tn:
            tk, tn = "apexclass", tn.split(".", 1)[0]
        if tk in _SUB_KINDS:
            continue
        target = f"salesforce:{tk}/{tn}"
        if target not in seen:
            seen.add(target)
            links.append({"kind": "references", "to": target})
    return links


def _file_to_ku(path: Path, nodes, raw_edges, root: Path):
    """Build one raw KU for a handled source file. The primary (container) node is
    the file's first non-sub node — extractors emit it first (object before its
    fields, class before its methods, flow before its elements)."""
    tops = [n for n in nodes if n.get("type") not in _SUB_KINDS] or nodes
    primary = tops[0]
    ptype = primary.get("type")
    pname = _name_seg(primary["id"])

    entities = list(_entities_for(nodes))
    for e in raw_edges:
        if e.get("type") in _SUBJECT_EDGE_TYPES and e.get("to_kind") == "object" and e.get("to_name"):
            entities.append(e["to_name"])
    # stable de-dup, primary name first
    seen: set = set()
    ents: list = []
    for nm in [pname] + entities:
        if nm and nm not in seen:
            seen.add(nm)
            ents.append(nm)

    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:                                   # pragma: no cover
        rel = path.name
    body = path.read_text("utf-8", errors="replace")

    ku = KnowledgeUnit(
        id=f"salesforce:{ptype}/{pname}",
        kind="source-record", tier="raw", source="salesforce",
        path=f"kb/raw/salesforce/{rel}",
        title=primary.get("label") or pname,
        entities=ents,
        links=_links_for(raw_edges),
        confidence="VERIFIED",
        provenance={"node_type": ptype, "source_path": rel},
    )
    return ku, body


def _graph_ku(graph):
    body = _gb_persistence.to_json(graph)   # deterministic; carries version + the 4 keys
    ku = KnowledgeUnit(
        id=GRAPH_ID, kind="graph", tier="structured", source="salesforce",
        path=GRAPH_PATH, title="Salesforce metadata graph (graph-builder)",
        confidence="VERIFIED",
    )
    return ku, body


def digest(force_app_dir) -> Digest:
    """Parse a force-app tree into a :class:`Digest` (pure; no Librarian).

    Single extraction pass: the engine's two-phase API extracts every file once,
    the per-file results become the raw KUs, and the SAME results are resolved
    into the graph — previously the tree was extracted twice (once inside
    ``build_graph``, once for the KUs), doubling parse cost on large orgs. The
    graph is built from the Salesforce extractors only (a force-app never
    matches the Confluence/Jira/Mule extractors, so the output is identical)."""
    root = Path(force_app_dir)
    builder = (_GraphBuilder().register(*_sf_extractors())
               .register_resolver(*_gb_default_resolvers()))
    paths = sorted(p for p in root.rglob("*") if p.is_file())
    extracted, errors = builder.extract_files(paths, root=root)
    # translation files emit only partial attribute-donor nodes (label_<locale>);
    # they enrich the graph but must not mint KUs — their primary node id would
    # collide with the real object/field KU minted from the defining file
    kus = [_file_to_ku(path, nodes, raw_edges, root)
           for path, nodes, raw_edges in extracted
           if any(not n.get("partial") for n in nodes)]
    graph = builder.resolve_extracted(extracted, errors)
    kus.append(_graph_ku(graph))
    # extraction failures live in graph["errors"]; mirror the old skipped strings
    skipped = [f"{e['path']}: {e['error']}" for e in graph["errors"]]
    return Digest(graph=graph, kus=kus, skipped=skipped)


# --------------------------------------------------------------------------- #
# built-in tool KU (records the vendored engine version + pin)
# --------------------------------------------------------------------------- #
def _tool_provenance() -> dict:
    return {
        "package": "graphbuilder",
        "vendored_sha": _VENDORED_SHA,
        "vendored_at": _VENDORED_AT,
        "source_repo": "graph-builder (private)",
        "runtime": "stdlib-only (regex Apex backend; tree-sitter AST not vendored)",
        "doc": "vendor/README.md",
    }


def _tool_ku():
    return KnowledgeUnit(
        id=TOOL_ID, kind="tool", tier="built-in", source="agent",
        path=TOOL_PATH,
        title="graph-builder — vendored Salesforce metadata parsing engine",
        entities=["graphbuilder"], confidence="VERIFIED",
        provenance=_tool_provenance(),
    ), json.dumps(_tool_provenance(), ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# ingest
# --------------------------------------------------------------------------- #
def ingest_salesforce(lib, force_app_dir, author, rationale):
    """Parse a force-app tree and commit it through the Librarian. Returns
    ``(Report, Digest)``. Re-ingesting unchanged content is a no-op (I9); the
    built-in engine KU is registered once (idempotent)."""
    dg = digest(force_app_dir)
    txn = lib.begin(author, rationale)
    if lib.get(TOOL_ID) is None:
        tool_ku, tool_body = _tool_ku()
        txn.add_ku(tool_ku, body=tool_body)
    for ku, body in dg.kus:
        txn.ingest_ku(ku, body=body)
    return txn.commit(), dg


# --------------------------------------------------------------------------- #
# runtime graph queries — same names/returns as the old digest. See §4.1 of
# MASTER_PROMPT.md (imported there as ``sf``).
# --------------------------------------------------------------------------- #
def load_graph(lib) -> dict:
    body = lib.read_body(GRAPH_ID)
    if not body:
        return {"nodes": [], "edges": [], "unresolved": [], "errors": []}
    return json.loads(body)


def _node(graph, nid):
    for n in graph["nodes"]:
        if n["id"] == nid:
            return n
    return None


def fields_of(graph, object_name) -> list:
    """Fields of an object: ``[{"name", "type"}]``. Engine emits
    ``field --field_of--> object`` with the type in the ``field_type`` attr."""
    oid = f"object/{object_name}"
    out = []
    for e in graph["edges"]:
        if e["type"] == "field_of" and e["dst"] == oid:
            n = _node(graph, e["src"])
            if n:
                out.append({"name": _name_seg(n["id"]).split(".", 1)[-1],
                            "type": n.get("field_type", n.get("ftype", ""))})
    return out


def triggers_on(graph, object_name) -> list:
    """Apex triggers on an object. Several kinds use ``--on--> object`` (sharing
    rules, approval processes, assignment/escalation rules, reports…), so this
    filters to ``trigger`` sources; use :func:`neighbors` for the others."""
    oid = f"object/{object_name}"
    out = []
    for e in graph["edges"]:
        if e["type"] == "on" and e["dst"] == oid:
            n = _node(graph, e["src"])
            if n and n.get("type") == "trigger":
                out.append(_name_seg(e["src"]))
    return out


def _methods_of(graph, class_name) -> set:
    """Method node ids contained by an Apex class (``apexclass --contains-->
    apexmethod``). The engine resolves qualified calls to the *method* node when
    it can, so a class's callers may target its methods rather than the class."""
    cid = f"apexclass/{class_name}"
    return {e["dst"] for e in graph["edges"]
            if e["type"] == "contains" and e["src"] == cid
            and e["dst"].startswith("apexmethod/")}


def who_calls(graph, class_name) -> list:
    """Everything that calls an Apex class (triggers, flows, LWC, Aura, VF, other
    classes, OmniStudio …) — full node ids of the callers. Counts callers of the
    class *and* of any of its methods (the engine resolves calls to method nodes
    when it can), de-duplicated in encounter order."""
    targets = {f"apexclass/{class_name}"} | _methods_of(graph, class_name)
    out: list = []
    seen: set = set()
    for e in graph["edges"]:
        if e["type"] == "calls" and e["dst"] in targets and e["src"] not in seen:
            seen.add(e["src"])
            out.append(e["src"])
    return out


def calls_of(graph, class_name) -> list:
    """Names of the classes an Apex class calls. Method targets (``apexmethod/
    Cls.m``) are reduced to their class (``Cls``); class targets pass through.
    De-duplicated."""
    cid = f"apexclass/{class_name}"
    out: list = []
    seen: set = set()
    for e in graph["edges"]:
        if e["type"] != "calls" or e["src"] != cid:
            continue
        seg = _name_seg(e["dst"])
        name = seg.split(".", 1)[0] if e["dst"].startswith("apexmethod/") else seg
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def flows_touching(graph, object_name) -> list:
    """Flows that touch an object — directly (``flow --touches--> object``) or via
    one of their elements reading/writing/touching it (the element carries its
    owning ``flow`` name). De-duplicated."""
    oid = f"object/{object_name}"
    out: list = []
    seen: set = set()

    def _add(name):
        if name and name not in seen:
            seen.add(name)
            out.append(name)

    for e in graph["edges"]:
        if e["dst"] != oid or e["type"] not in ("touches", "reads", "writes"):
            continue
        src = _node(graph, e["src"])
        if src is None:
            continue
        if src.get("type") == "flow":
            _add(_name_seg(e["src"]))
        elif src.get("type") == "flowelement" and src.get("flow"):
            _add(src["flow"])
    return out


def components_using(graph, class_name) -> list:
    """LWC/Aura/VF/flows/triggers/classes that call an Apex class (== who_calls)."""
    return who_calls(graph, class_name)


def neighbors(graph, node_id, direction="out", edge_type=None) -> list:
    """Generic walk: ``out`` returns dsts of edges from ``node_id``; ``in`` returns
    srcs of edges into it. Optionally filter by ``edge_type``."""
    out = []
    for e in graph["edges"]:
        if edge_type and e["type"] != edge_type:
            continue
        if direction == "out" and e["src"] == node_id:
            out.append(e["dst"])
        elif direction == "in" and e["dst"] == node_id:
            out.append(e["src"])
    return out


def grants_on(graph, object_name) -> list:
    """Permission sets / profiles granting access to an object."""
    return neighbors(graph, f"object/{object_name}", "in", "grants")


def pages_for(graph, object_name) -> list:
    """Lightning pages / layouts built for an object (``--page-for--> object``)."""
    return neighbors(graph, f"object/{object_name}", "in", "page-for")


def dependencies(graph, node_id) -> list:
    """(edge_type, target) for everything ``node_id`` points at — its outgoing deps."""
    return [(e["type"], e["dst"]) for e in graph["edges"] if e["src"] == node_id]


def dependents(graph, node_id) -> list:
    """(edge_type, source) for everything that points at ``node_id`` — impact analysis."""
    return [(e["type"], e["src"]) for e in graph["edges"] if e["dst"] == node_id]
