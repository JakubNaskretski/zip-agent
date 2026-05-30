"""Salesforce digest — parse a `force-app` source tree into KUs + a typed graph.

Granularity (§2.3): one KU per custom object, per Apex class, per trigger; plus a
single derived graph KU. Fields/methods are graph nodes, not manifest entries.

Parsing is dependency-free (stdlib `xml.etree` + regex for Apex), so it runs in
the sandbox with no wheelhouse. tree-sitter-apex can replace the Apex regex later
for precision; the graph contract stays the same.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from ..schema import KnowledgeUnit
from . import omnistudio

_NS = {"sf": "http://soap.sforce.com/2006/04/metadata"}


# --------------------------------------------------------------------------- #
# parsed shapes
# --------------------------------------------------------------------------- #
@dataclass
class SFField:
    name: str
    type: str = ""
    label: str = ""
    reference_to: str = ""        # for Lookup / MasterDetail


@dataclass
class SFObject:
    name: str
    label: str = ""
    fields: list = field(default_factory=list)            # list[SFField]
    validation_rules: list = field(default_factory=list)  # list[str]


@dataclass
class SFClass:
    name: str
    extends: str = ""
    implements: list = field(default_factory=list)
    sobject_refs: set = field(default_factory=set)        # custom objects referenced
    class_refs: set = field(default_factory=set)          # other apex classes referenced
    kind: str = "class"                                   # class | batch | schedulable
    source: str = ""


@dataclass
class SFTrigger:
    name: str
    sobject: str = ""
    events: str = ""
    class_refs: set = field(default_factory=set)
    source: str = ""


@dataclass
class SFFlow:
    name: str
    process_type: str = ""
    trigger_object: str = ""               # start object for record-triggered flows
    objects: set = field(default_factory=set)       # objects the flow touches
    class_refs: set = field(default_factory=set)    # apex invoked by the flow
    source: str = ""


@dataclass
class SFLwc:
    name: str
    class_refs: set = field(default_factory=set)    # apex controllers it imports
    lwc_refs: set = field(default_factory=set)       # other LWC it composes
    source: str = ""


@dataclass
class SFFlexiPage:
    name: str
    sobject: str = ""                                # the object the page is for
    lwc_refs: set = field(default_factory=set)       # custom components it embeds
    components: list = field(default_factory=list)   # all component names (incl standard)
    source: str = ""


@dataclass
class SFAccess:
    """A permission set or profile — both grant the same things, same tags."""
    name: str
    kind: str                                        # permissionset | profile
    label: str = ""
    objects: set = field(default_factory=set)        # objectPermissions
    fields: set = field(default_factory=set)         # fieldPermissions (Object.Field)
    classes: set = field(default_factory=set)        # classAccesses
    source: str = ""


@dataclass
class SFPermSetGroup:
    name: str
    label: str = ""
    permsets: set = field(default_factory=set)
    source: str = ""


@dataclass
class SFDigest:
    objects: list = field(default_factory=list)
    classes: list = field(default_factory=list)
    triggers: list = field(default_factory=list)
    flows: list = field(default_factory=list)
    lwc: list = field(default_factory=list)
    flexipages: list = field(default_factory=list)
    accesses: list = field(default_factory=list)     # permission sets + profiles
    permsetgroups: list = field(default_factory=list)
    omni: list = field(default_factory=list)         # OmniStudio components (provisional)
    graph: dict = field(default_factory=dict)
    skipped: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def _text(el, tag):
    child = el.find(f"sf:{tag}", _NS)
    return child.text if child is not None and child.text is not None else ""


def parse_field(path: Path) -> SFField | None:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    return SFField(
        name=_text(root, "fullName") or path.stem,
        type=_text(root, "type"),
        label=_text(root, "label"),
        reference_to=_text(root, "referenceTo"),
    )


def parse_object(obj_dir: Path) -> SFObject:
    name = obj_dir.name
    label = name
    meta = obj_dir / f"{name}.object-meta.xml"
    if meta.exists():
        try:
            label = _text(ET.parse(meta).getroot(), "label") or name
        except ET.ParseError:
            pass
    fields = []
    for fp in sorted((obj_dir / "fields").glob("*.field-meta.xml")) if (obj_dir / "fields").is_dir() else []:
        f = parse_field(fp)
        if f:
            fields.append(f)
    vrs = [p.stem.replace(".validationRule-meta", "")
           for p in sorted((obj_dir / "validationRules").glob("*.xml"))] \
        if (obj_dir / "validationRules").is_dir() else []
    return SFObject(name=name, label=label, fields=fields, validation_rules=vrs)


def _strip_apex(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)   # block comments
    src = re.sub(r"//[^\n]*", " ", src)                # line comments
    return src


def parse_apex(path: Path) -> SFClass:
    raw = path.read_text("utf-8", errors="replace")
    s = _strip_apex(raw)
    name = path.stem
    m = re.search(r"\bclass\s+(\w+)", s)
    if m:
        name = m.group(1)
    extends = (re.search(r"\bextends\s+([\w.]+)", s) or [None, ""])[1]
    impl_m = re.search(r"\bimplements\s+([\w.,\s]+?)\s*\{", s)
    implements = [i.strip() for i in impl_m.group(1).split(",")] if impl_m else []
    kind = "class"
    impl_join = " ".join(implements)
    if "Batchable" in impl_join:
        kind = "batch"
    elif "Schedulable" in impl_join:
        kind = "schedulable"
    sobj = set(re.findall(r"\b(\w+__c)\b", s))                    # custom objects/fields
    sobj |= set(re.findall(r"\bFROM\s+(\w+)", s, re.I))          # SOQL targets
    return SFClass(name=name, extends=extends, implements=implements,
                   sobject_refs=sobj, kind=kind, source=raw)


def parse_trigger(path: Path) -> SFTrigger:
    raw = path.read_text("utf-8", errors="replace")
    s = _strip_apex(raw)
    m = re.search(r"\btrigger\s+(\w+)\s+on\s+(\w+)\s*\(([^)]*)\)", s)
    name = m.group(1) if m else path.stem
    sobject = m.group(2) if m else ""
    events = " ".join(m.group(3).split()) if m else ""
    return SFTrigger(name=name, sobject=sobject, events=events, source=raw)


def parse_flow(path: Path) -> SFFlow:
    raw = path.read_text("utf-8", errors="replace")
    name = path.name.replace(".flow-meta.xml", "")
    flow = SFFlow(name=name, source=raw)
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return flow
    flow.process_type = _text(root, "processType")
    # objects the flow touches (recordCreates/Updates/Lookups/Deletes/start all use <object>)
    flow.objects = {el.text for el in root.iter(f"{{{_NS['sf']}}}object") if el.text}
    start = root.find("sf:start", _NS)
    if start is not None:
        flow.trigger_object = _text(start, "object")
    # apex invoked from action calls
    for ac in root.iter(f"{{{_NS['sf']}}}actionCalls"):
        if _text(ac, "actionType") == "apex":
            cls = _text(ac, "actionName")
            if cls:
                flow.class_refs.add(cls)
    return flow


_APEX_IMPORT = re.compile(r"@salesforce/apex/(\w+)\.\w+")
_LWC_IMPORT = re.compile(r"""from\s+['"]c/(\w+)['"]""")


def parse_lwc(bundle_dir: Path) -> SFLwc:
    name = bundle_dir.name
    js = bundle_dir / f"{name}.js"
    src = js.read_text("utf-8", errors="replace") if js.exists() else ""
    return SFLwc(
        name=name,
        class_refs=set(_APEX_IMPORT.findall(src)),
        lwc_refs=set(_LWC_IMPORT.findall(src)) - {name},
        source=src,
    )


def _iter_text(root, tag):
    """All text values of <tag> anywhere under root (namespaced)."""
    return [el.text for el in root.iter(f"{{{_NS['sf']}}}{tag}") if el.text]


def parse_flexipage(path: Path) -> SFFlexiPage:
    name = path.name.replace(".flexipage-meta.xml", "")
    raw = path.read_text("utf-8", errors="replace")
    fp = SFFlexiPage(name=name, source=raw)
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return fp
    fp.sobject = _text(root, "sobjectType")
    fp.components = _iter_text(root, "componentName")
    # custom LWC/Aura are referenced as "c:componentName"
    fp.lwc_refs = {c.split(":", 1)[1] for c in fp.components if c.startswith("c:")}
    return fp


def parse_access(path: Path, kind: str) -> SFAccess:
    """Permission set or profile — identical grant structure."""
    name = path.name.replace(f".{kind}-meta.xml", "")
    raw = path.read_text("utf-8", errors="replace")
    acc = SFAccess(name=name, kind=kind, source=raw)
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return acc
    acc.label = _text(root, "label") or name
    # names come straight from the metadata, so standard/custom/packaged all work
    for op in root.iter(f"{{{_NS['sf']}}}objectPermissions"):
        o = _text(op, "object")
        if o:
            acc.objects.add(o)
    for fp_ in root.iter(f"{{{_NS['sf']}}}fieldPermissions"):
        f = _text(fp_, "field")
        if f:
            acc.fields.add(f)
    for ca in root.iter(f"{{{_NS['sf']}}}classAccesses"):
        c = _text(ca, "apexClass")
        if c:
            acc.classes.add(c)
    return acc


def parse_permsetgroup(path: Path) -> SFPermSetGroup:
    name = path.name.replace(".permissionsetgroup-meta.xml", "")
    raw = path.read_text("utf-8", errors="replace")
    psg = SFPermSetGroup(name=name, source=raw)
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return psg
    psg.label = _text(root, "label") or name
    psg.permsets = set(_iter_text(root, "permissionSets"))
    return psg


def parse_salesforce(force_app_dir) -> SFDigest:
    """Parse a force-app source tree into an SFDigest (pure; no Librarian)."""
    root = Path(force_app_dir)
    base = root
    if (root / "main" / "default").is_dir():
        base = root / "main" / "default"
    elif not (root / "objects").is_dir():
        # maybe they pointed at the project root containing force-app
        for cand in root.rglob("main/default"):
            base = cand
            break

    d = SFDigest()

    obj_dir = base / "objects"
    if obj_dir.is_dir():
        for od in sorted(p for p in obj_dir.iterdir() if p.is_dir()):
            try:
                d.objects.append(parse_object(od))
            except Exception as e:                      # pragma: no cover
                d.skipped.append(f"object {od.name}: {e}")

    cls_dir = base / "classes"
    if cls_dir.is_dir():
        for cp in sorted(cls_dir.glob("*.cls")):
            try:
                d.classes.append(parse_apex(cp))
            except Exception as e:                      # pragma: no cover
                d.skipped.append(f"class {cp.name}: {e}")

    trg_dir = base / "triggers"
    if trg_dir.is_dir():
        for tp in sorted(trg_dir.glob("*.trigger")):
            try:
                d.triggers.append(parse_trigger(tp))
            except Exception as e:                      # pragma: no cover
                d.skipped.append(f"trigger {tp.name}: {e}")

    flow_dir = base / "flows"
    if flow_dir.is_dir():
        for fp in sorted(flow_dir.glob("*.flow-meta.xml")):
            try:
                d.flows.append(parse_flow(fp))
            except Exception as e:                      # pragma: no cover
                d.skipped.append(f"flow {fp.name}: {e}")

    lwc_dir = base / "lwc"
    if lwc_dir.is_dir():
        for bd in sorted(p for p in lwc_dir.iterdir() if p.is_dir()):
            if (bd / f"{bd.name}.js").exists():
                try:
                    d.lwc.append(parse_lwc(bd))
                except Exception as e:                  # pragma: no cover
                    d.skipped.append(f"lwc {bd.name}: {e}")

    fp_dir = base / "flexipages"
    if fp_dir.is_dir():
        for fp in sorted(fp_dir.glob("*.flexipage-meta.xml")):
            try:
                d.flexipages.append(parse_flexipage(fp))
            except Exception as e:                      # pragma: no cover
                d.skipped.append(f"flexipage {fp.name}: {e}")

    for kind, suffix in (("permissionset", "permissionsets"), ("profile", "profiles")):
        adir = base / suffix
        if adir.is_dir():
            for ap in sorted(adir.glob(f"*.{kind}-meta.xml")):
                try:
                    d.accesses.append(parse_access(ap, kind))
                except Exception as e:                  # pragma: no cover
                    d.skipped.append(f"{kind} {ap.name}: {e}")

    psg_dir = base / "permissionsetgroups"
    if psg_dir.is_dir():
        for pg in sorted(psg_dir.glob("*.permissionsetgroup-meta.xml")):
            try:
                d.permsetgroups.append(parse_permsetgroup(pg))
            except Exception as e:                      # pragma: no cover
                d.skipped.append(f"permsetgroup {pg.name}: {e}")

    d.omni = omnistudio.parse_omnistudio(base)

    _resolve_refs(d)
    d.graph = build_graph(d)
    return d


def _resolve_refs(d: SFDigest):
    """Pass 2: resolve class-to-class references now that all names are known."""
    class_names = {c.name for c in d.classes}
    object_names = {o.name for o in d.objects}
    for c in d.classes:
        s = _strip_apex(c.source)
        tokens = set(re.findall(r"\b([A-Z]\w+)\b", s))
        c.class_refs = (tokens & class_names) - {c.name}
        c.sobject_refs &= object_names                  # keep only known custom objects
    for t in d.triggers:
        s = _strip_apex(t.source)
        tokens = set(re.findall(r"\b([A-Z]\w+)\b", s))
        t.class_refs = tokens & class_names
    lwc_names = {l.name for l in d.lwc}
    for fl in d.flows:
        fl.class_refs &= class_names              # keep only resolvable apex
    for l in d.lwc:
        l.class_refs &= class_names
        l.lwc_refs &= lwc_names

    permset_names = {a.name for a in d.accesses if a.kind == "permissionset"}
    for fp in d.flexipages:
        fp.lwc_refs &= lwc_names
    for psg in d.permsetgroups:
        psg.permsets &= permset_names
    # OmniStudio (provisional): constrain cross-refs to known names
    omni_ip = {o.name for o in d.omni if o.otype == "integrationprocedure"}
    omni_dm = {o.name for o in d.omni if o.otype == "datamapper"}
    for o in d.omni:
        o.ip_refs &= omni_ip
        o.dm_refs &= omni_dm
        o.apex_refs &= class_names
        o.lwc_refs &= lwc_names
        # object_refs are NOT constrained — standard/packaged objects get external nodes


# --------------------------------------------------------------------------- #
# graph
# --------------------------------------------------------------------------- #
def build_graph(d: SFDigest) -> dict:
    nodes, edges = [], []
    seen = set()
    obj_names = {o.name for o in d.objects}
    cls_names = {c.name for c in d.classes}
    lwc_names = {l.name for l in d.lwc}

    def node(nid, ntype, label="", **attrs):
        if nid not in seen:
            seen.add(nid)
            nodes.append({"id": nid, "type": ntype, "label": label or nid, **attrs})

    def obj_node(name):
        """Object node id; a referenced standard/packaged object we didn't
        retrieve gets a lightweight external stub so the edge still forms."""
        nid = f"object/{name}"
        node(nid, "object", name, external=name not in obj_names)
        return nid

    # pass 1: all parsed object + field nodes (before any external stubs)
    for o in d.objects:
        node(f"object/{o.name}", "object", o.label, external=False,
             validation_rules=o.validation_rules)
        for f in o.fields:
            node(f"field/{o.name}.{f.name}", "field", f.name, ftype=f.type)
            edges.append({"src": f"object/{o.name}", "dst": f"field/{o.name}.{f.name}",
                          "type": "field_of"})
    # pass 2: lookup edges (parsed objects exist; external targets get stubs)
    for o in d.objects:
        for f in o.fields:
            if f.reference_to:
                edges.append({"src": f"field/{o.name}.{f.name}",
                              "dst": obj_node(f.reference_to), "type": "lookup"})

    for c in d.classes:
        cid = f"apexclass/{c.name}"
        node(cid, "apexclass", c.name, kind=c.kind, extends=c.extends, implements=c.implements)
    for c in d.classes:
        cid = f"apexclass/{c.name}"
        for ref in sorted(c.class_refs):
            edges.append({"src": cid, "dst": f"apexclass/{ref}", "type": "calls"})
        for so in sorted(c.sobject_refs):
            edges.append({"src": cid, "dst": obj_node(so), "type": "references"})

    for t in d.triggers:
        tid = f"trigger/{t.name}"
        node(tid, "trigger", t.name, events=t.events)
        if t.sobject:
            edges.append({"src": tid, "dst": obj_node(t.sobject), "type": "on"})
        for ref in sorted(t.class_refs):
            edges.append({"src": tid, "dst": f"apexclass/{ref}", "type": "calls"})

    for fl in d.flows:
        fid = f"flow/{fl.name}"
        node(fid, "flow", fl.name, process_type=fl.process_type, trigger_object=fl.trigger_object)
        for obj in sorted(fl.objects):
            edges.append({"src": fid, "dst": obj_node(obj), "type": "touches"})
        for ref in sorted(fl.class_refs):
            edges.append({"src": fid, "dst": f"apexclass/{ref}", "type": "calls"})

    for l in d.lwc:
        lid = f"lwc/{l.name}"
        node(lid, "lwc", l.name)
        for ref in sorted(l.class_refs):
            edges.append({"src": lid, "dst": f"apexclass/{ref}", "type": "calls"})
        for ref in sorted(l.lwc_refs):
            edges.append({"src": lid, "dst": f"lwc/{ref}", "type": "uses-component"})

    for fp in d.flexipages:
        pid = f"flexipage/{fp.name}"
        node(pid, "flexipage", fp.name)
        if fp.sobject:
            edges.append({"src": pid, "dst": obj_node(fp.sobject), "type": "page-for"})
        for ref in sorted(fp.lwc_refs & lwc_names):
            edges.append({"src": pid, "dst": f"lwc/{ref}", "type": "embeds"})

    for a in d.accesses:
        aid = f"{a.kind}/{a.name}"
        node(aid, a.kind, a.label or a.name)
        # coarse edges to objects/classes (field grants kept in the KU body, not the graph)
        for o in sorted(a.objects):
            edges.append({"src": aid, "dst": obj_node(o), "type": "grants"})
        for c in sorted(a.classes & cls_names):
            edges.append({"src": aid, "dst": f"apexclass/{c}", "type": "grants"})

    for psg in d.permsetgroups:
        gid = f"permsetgroup/{psg.name}"
        node(gid, "permsetgroup", psg.label or psg.name)
        for ps in sorted(psg.permsets):
            edges.append({"src": gid, "dst": f"permissionset/{ps}", "type": "contains"})

    for o in d.omni:
        oid = f"{o.otype}/{o.name}"
        node(oid, o.otype, o.name, model=o.model, subtype=o.subtype)
        for ref in sorted(o.ip_refs):
            edges.append({"src": oid, "dst": f"integrationprocedure/{ref}", "type": "calls"})
        for ref in sorted(o.dm_refs):
            edges.append({"src": oid, "dst": f"datamapper/{ref}", "type": "uses"})
        for ref in sorted(o.apex_refs):
            edges.append({"src": oid, "dst": f"apexclass/{ref}", "type": "calls"})
        for ref in sorted(o.lwc_refs):
            edges.append({"src": oid, "dst": f"lwc/{ref}", "type": "embeds"})
        rel = "maps" if o.otype == "datamapper" else "touches"
        for ref in sorted(o.object_refs):
            edges.append({"src": oid, "dst": obj_node(ref), "type": rel})

    return {"nodes": nodes, "edges": edges}


# --------------------------------------------------------------------------- #
# KU construction + ingest
# --------------------------------------------------------------------------- #
def _refs_links(targets):
    return [{"kind": "references", "to": t} for t in targets]


def to_kus(d: SFDigest):
    """Yield (KnowledgeUnit, body) pairs for everything in the digest."""
    obj_names = {o.name for o in d.objects}
    cls_names = {c.name for c in d.classes}
    lwc_names = {l.name for l in d.lwc}
    permset_names = {a.name for a in d.accesses if a.kind == "permissionset"}

    for o in d.objects:
        body = json.dumps({
            "name": o.name, "label": o.label,
            "fields": [vars(f) for f in o.fields],
            "validationRules": o.validation_rules,
        }, ensure_ascii=False, indent=2)
        links = _refs_links(
            f"salesforce:object/{f.reference_to}" for f in o.fields
            if f.reference_to in obj_names and f.reference_to != o.name)
        yield KnowledgeUnit(
            id=f"salesforce:object/{o.name}", kind="source-record", tier="raw",
            source="salesforce", path=f"kb/raw/salesforce/objects/{o.name}.json",
            title=o.label, entities=[o.name] + [f.name for f in o.fields],
            links=links, confidence="VERIFIED",
        ), body

    for c in d.classes:
        links = _refs_links(
            [f"salesforce:apexclass/{r}" for r in sorted(c.class_refs)]
            + [f"salesforce:object/{r}" for r in sorted(c.sobject_refs)])
        yield KnowledgeUnit(
            id=f"salesforce:apexclass/{c.name}", kind="source-record", tier="raw",
            source="salesforce", path=f"kb/raw/salesforce/classes/{c.name}.cls",
            title=c.name, entities=[c.name], links=links, confidence="VERIFIED",
            provenance={"apex_kind": c.kind, "implements": c.implements},
        ), c.source

    for t in d.triggers:
        targets = ([f"salesforce:object/{t.sobject}"] if t.sobject in obj_names else []) \
            + [f"salesforce:apexclass/{r}" for r in sorted(t.class_refs) if r in cls_names]
        yield KnowledgeUnit(
            id=f"salesforce:trigger/{t.name}", kind="source-record", tier="raw",
            source="salesforce", path=f"kb/raw/salesforce/triggers/{t.name}.trigger",
            title=t.name, entities=[t.name, t.sobject], links=_refs_links(targets),
            confidence="VERIFIED", provenance={"on": t.sobject, "events": t.events},
        ), t.source

    for fl in d.flows:
        targets = [f"salesforce:object/{o}" for o in sorted(fl.objects) if o in obj_names] \
            + [f"salesforce:apexclass/{r}" for r in sorted(fl.class_refs)]
        yield KnowledgeUnit(
            id=f"salesforce:flow/{fl.name}", kind="source-record", tier="raw",
            source="salesforce", path=f"kb/raw/salesforce/flows/{fl.name}.flow-meta.xml",
            title=fl.name, entities=[fl.name] + sorted(fl.objects), links=_refs_links(targets),
            confidence="VERIFIED",
            provenance={"process_type": fl.process_type, "trigger_object": fl.trigger_object},
        ), fl.source

    for l in d.lwc:
        targets = [f"salesforce:apexclass/{r}" for r in sorted(l.class_refs)] \
            + [f"salesforce:lwc/{r}" for r in sorted(l.lwc_refs)]
        yield KnowledgeUnit(
            id=f"salesforce:lwc/{l.name}", kind="source-record", tier="raw",
            source="salesforce", path=f"kb/raw/salesforce/lwc/{l.name}.js",
            title=l.name, entities=[l.name], links=_refs_links(targets), confidence="VERIFIED",
        ), l.source

    for fp in d.flexipages:
        targets = ([f"salesforce:object/{fp.sobject}"] if fp.sobject in obj_names else []) \
            + [f"salesforce:lwc/{r}" for r in sorted(fp.lwc_refs & lwc_names)]
        yield KnowledgeUnit(
            id=f"salesforce:flexipage/{fp.name}", kind="source-record", tier="raw",
            source="salesforce", path=f"kb/raw/salesforce/flexipages/{fp.name}.flexipage-meta.xml",
            title=fp.name, entities=[fp.name] + ([fp.sobject] if fp.sobject else []),
            links=_refs_links(targets), confidence="VERIFIED",
            provenance={"sobject": fp.sobject},
        ), fp.source

    for a in d.accesses:
        targets = [f"salesforce:object/{o}" for o in sorted(a.objects & obj_names)] \
            + [f"salesforce:apexclass/{c}" for c in sorted(a.classes & cls_names)]
        yield KnowledgeUnit(
            id=f"salesforce:{a.kind}/{a.name}", kind="source-record", tier="raw",
            source="salesforce", path=f"kb/raw/salesforce/{a.kind}s/{a.name}.{a.kind}-meta.xml",
            title=a.label or a.name, entities=[a.name] + sorted(a.objects),
            links=_refs_links(targets), confidence="VERIFIED",
            provenance={"grants_objects": sorted(a.objects), "grants_fields": sorted(a.fields),
                        "grants_classes": sorted(a.classes)},
        ), a.source

    for psg in d.permsetgroups:
        targets = [f"salesforce:permissionset/{p}" for p in sorted(psg.permsets & permset_names)]
        yield KnowledgeUnit(
            id=f"salesforce:permsetgroup/{psg.name}", kind="source-record", tier="raw",
            source="salesforce",
            path=f"kb/raw/salesforce/permissionsetgroups/{psg.name}.permissionsetgroup-meta.xml",
            title=psg.label or psg.name, entities=[psg.name], links=_refs_links(targets),
            confidence="VERIFIED",
        ), psg.source

    for o in d.omni:
        targets = [f"salesforce:integrationprocedure/{r}" for r in sorted(o.ip_refs)] \
            + [f"salesforce:datamapper/{r}" for r in sorted(o.dm_refs)] \
            + [f"salesforce:apexclass/{r}" for r in sorted(o.apex_refs)] \
            + [f"salesforce:lwc/{r}" for r in sorted(o.lwc_refs)] \
            + [f"salesforce:object/{r}" for r in sorted(o.object_refs)]
        yield KnowledgeUnit(
            id=f"salesforce:{o.otype}/{o.name}", kind="source-record", tier="raw",
            source="salesforce", path=f"kb/raw/salesforce/omnistudio/{o.otype}/{o.name}.json",
            title=o.name, entities=[o.name] + sorted(o.object_refs), links=_refs_links(targets),
            confidence="VERIFIED", provenance={"model": o.model, "subtype": o.subtype},
        ), o.source

    yield KnowledgeUnit(
        id="salesforce:graph/sf", kind="graph", tier="structured", source="salesforce",
        path="kb/structured/salesforce/graph.json", title="Salesforce object/class/trigger graph",
        confidence="VERIFIED",
    ), json.dumps(d.graph, ensure_ascii=False, indent=2)


def ingest_salesforce(lib, force_app_dir, author, rationale):
    """Parse a force-app tree and commit it through the Librarian. Returns
    (Report, SFDigest)."""
    d = parse_salesforce(force_app_dir)
    txn = lib.begin(author, rationale)
    for ku, body in to_kus(d):
        txn.ingest_ku(ku, body=body)
    return txn.commit(), d


# --------------------------------------------------------------------------- #
# runtime graph queries
# --------------------------------------------------------------------------- #
def load_graph(lib) -> dict:
    body = lib.read_body("salesforce:graph/sf")
    return json.loads(body) if body else {"nodes": [], "edges": []}


def _node(graph, nid):
    for n in graph["nodes"]:
        if n["id"] == nid:
            return n
    return None


def fields_of(graph, object_name) -> list:
    oid = f"object/{object_name}"
    out = []
    for e in graph["edges"]:
        if e["type"] == "field_of" and e["src"] == oid:
            n = _node(graph, e["dst"])
            if n:
                out.append({"name": n["label"], "type": n.get("ftype", "")})
    return out


def triggers_on(graph, object_name) -> list:
    oid = f"object/{object_name}"
    return [e["src"].split("/", 1)[1] for e in graph["edges"]
            if e["type"] == "on" and e["dst"] == oid]


def who_calls(graph, class_name) -> list:
    cid = f"apexclass/{class_name}"
    return [e["src"] for e in graph["edges"] if e["type"] == "calls" and e["dst"] == cid]


def calls_of(graph, class_name) -> list:
    cid = f"apexclass/{class_name}"
    return [e["dst"].split("/", 1)[1] for e in graph["edges"]
            if e["type"] == "calls" and e["src"] == cid]


def flows_touching(graph, object_name) -> list:
    oid = f"object/{object_name}"
    return [e["src"].split("/", 1)[1] for e in graph["edges"]
            if e["type"] == "touches" and e["dst"] == oid]


def components_using(graph, class_name) -> list:
    """LWC components (and flows/triggers/classes) that call an Apex class."""
    cid = f"apexclass/{class_name}"
    return [e["src"] for e in graph["edges"] if e["type"] == "calls" and e["dst"] == cid]


def neighbors(graph, node_id, direction="out", edge_type=None) -> list:
    """Generic walk: direction 'out' returns dsts of edges from node_id;
    'in' returns srcs of edges into it. Optionally filter by edge_type."""
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
    """Lightning pages (flexipages) built for an object."""
    return neighbors(graph, f"object/{object_name}", "in", "page-for")


def dependencies(graph, node_id) -> list:
    """(edge_type, target) for everything node_id points at — its outgoing deps."""
    return [(e["type"], e["dst"]) for e in graph["edges"] if e["src"] == node_id]


def dependents(graph, node_id) -> list:
    """(edge_type, source) for everything that points at node_id — impact analysis."""
    return [(e["type"], e["src"]) for e in graph["edges"] if e["dst"] == node_id]
