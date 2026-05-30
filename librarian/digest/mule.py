"""MuleSoft digest — parse a Mule app into KUs + a flow graph.

Granularity (§2.3): one KU per Mule **config file**; flows / sub-flows /
connectors are graph nodes. Dependency-free (stdlib `xml.etree`) and
namespace-tolerant — Mule uses many connector namespaces, so we work off local
tag names and the connector segment of the namespace URI.

Graph:
  - `muleflow/<name>`      nodes (attrs: kind = flow|sub-flow, file)
  - `muleconnector/<name>` nodes (http, db, salesforce, ee, …)
  - edges: muleflow --calls--> muleflow (via <flow-ref>),
           muleflow --uses--> muleconnector
External stub flow nodes are created for `<flow-ref>` targets not defined locally.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from ..schema import KnowledgeUnit

_MULE_SCHEMA = "www.mulesoft.org/schema/mule/"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _connector_of(tag: str) -> str:
    """The connector name from a Mule namespace URI (e.g. '…/mule/db' -> 'db')."""
    if not tag.startswith("{"):
        return ""
    uri = tag[1:].split("}", 1)[0]
    if _MULE_SCHEMA in uri:
        return uri.split(_MULE_SCHEMA, 1)[1].split("/")[0]
    return ""


@dataclass
class MuleFlow:
    name: str
    kind: str = "flow"                       # flow | sub-flow
    file: str = ""
    refs: set = field(default_factory=set)        # <flow-ref> targets
    connectors: set = field(default_factory=set)  # connector names used


@dataclass
class MuleFile:
    rel: str
    source: str
    flow_names: list = field(default_factory=list)


@dataclass
class MuleDigest:
    files: list = field(default_factory=list)     # MuleFile
    flows: list = field(default_factory=list)     # MuleFlow
    graph: dict = field(default_factory=dict)
    skipped: list = field(default_factory=list)


def parse_file(path: Path, rel: str):
    """Return (MuleFile, [MuleFlow]) or (None, []) if not a Mule config."""
    src = path.read_text("utf-8", errors="replace")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None, []
    if _local(root.tag) != "mule":
        return None, []
    flows = []
    for el in root.iter():
        ln = _local(el.tag)
        if ln in ("flow", "sub-flow") and el.get("name"):
            mf = MuleFlow(name=el.get("name"), kind=ln, file=rel)
            for d in el.iter():
                if _local(d.tag) == "flow-ref" and d.get("name"):
                    mf.refs.add(d.get("name"))
                else:
                    c = _connector_of(d.tag)
                    if c and c != "core":
                        mf.connectors.add(c)
            flows.append(mf)
    return MuleFile(rel=rel, source=src, flow_names=[f.name for f in flows]), flows


def parse_mule(mule_dir) -> MuleDigest:
    base = Path(mule_dir)
    if (base / "src" / "main" / "mule").is_dir():     # standard Mule app layout
        base = base / "src" / "main" / "mule"
    d = MuleDigest()
    for p in sorted(base.rglob("*.xml")):
        try:
            rel = p.relative_to(base).as_posix()
            mfile, flows = parse_file(p, rel)
        except Exception as e:                        # pragma: no cover
            d.skipped.append(f"{p.name}: {e}")
            continue
        if mfile is not None:
            d.files.append(mfile)
            d.flows.extend(flows)
    d.graph = build_graph(d)
    return d


def build_graph(d: MuleDigest) -> dict:
    nodes, edges, seen = [], [], set()

    def node(nid, ntype, label="", **attrs):
        if nid not in seen:
            seen.add(nid)
            nodes.append({"id": nid, "type": ntype, "label": label or nid, **attrs})

    flow_names = {f.name for f in d.flows}
    for f in d.flows:
        fid = f"muleflow/{f.name}"
        node(fid, "muleflow", f.name, kind=f.kind, file=f.file)
        for c in sorted(f.connectors):
            cid = f"muleconnector/{c}"
            node(cid, "muleconnector", c)
            edges.append({"src": fid, "dst": cid, "type": "uses"})
    for f in d.flows:
        fid = f"muleflow/{f.name}"
        for ref in sorted(f.refs):
            rid = f"muleflow/{ref}"
            node(rid, "muleflow", ref, external=ref not in flow_names)
            edges.append({"src": fid, "dst": rid, "type": "calls"})
    return {"nodes": nodes, "edges": edges}


def _refs_links(targets):
    return [{"kind": "references", "to": t} for t in targets]


def to_kus(d: MuleDigest):
    """One KU per Mule config file; entities = flow names + connectors. Cross-file
    flow-refs become file→file `references` links."""
    flow_to_file = {f.name: f.file for f in d.flows}
    refs_by_file: dict = {}
    conns_by_file: dict = {}
    for f in d.flows:
        for r in f.refs:
            tgt = flow_to_file.get(r)
            if tgt and tgt != f.file:
                refs_by_file.setdefault(f.file, set()).add(tgt)
        conns_by_file.setdefault(f.file, set()).update(f.connectors)

    for mf in d.files:
        ents = list(mf.flow_names) + sorted(conns_by_file.get(mf.rel, set()))
        links = _refs_links(sorted(f"mule:{t}" for t in refs_by_file.get(mf.rel, set())))
        yield KnowledgeUnit(
            id=f"mule:{mf.rel}", kind="source-record", tier="raw", source="mule",
            path=f"kb/raw/mule/{mf.rel}", title=mf.rel, entities=ents,
            links=links, confidence="VERIFIED",
        ), mf.source

    yield KnowledgeUnit(
        id="mule:graph/mule", kind="graph", tier="structured", source="mule",
        path="kb/structured/mule/graph.json", title="Mule flow graph", confidence="VERIFIED",
    ), json.dumps(d.graph, ensure_ascii=False, indent=2)


def ingest_mule(lib, mule_dir, author, rationale):
    """Parse a Mule app and commit it through the Librarian. Returns (Report, MuleDigest)."""
    d = parse_mule(mule_dir)
    txn = lib.begin(author, rationale)
    for ku, body in to_kus(d):
        txn.ingest_ku(ku, body=body)
    return txn.commit(), d


# --------------------------------------------------------------------------- #
# runtime graph queries
# --------------------------------------------------------------------------- #
def load_graph(lib) -> dict:
    body = lib.read_body("mule:graph/mule")
    return json.loads(body) if body else {"nodes": [], "edges": []}


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
