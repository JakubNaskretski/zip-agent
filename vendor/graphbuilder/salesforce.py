"""Salesforce parsers — turn `force-app` metadata files into typed dataclasses.

One record per custom object, Apex class, trigger, flow, LWC bundle, FlexiPage,
permission set/profile and permission-set group; fields and methods become graph
nodes downstream. Parsing is dependency-free (stdlib `xml.etree` + regex for
Apex). Captures names and structure only, never field or record values.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from . import omnistudio
from .xmlutil import child as _child, iter_local as _iter_local


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
    validation_rules: list = field(default_factory=list)  # rule names; TODO: unused (validates edges come from objects.py) — drop or repurpose


@dataclass
class SFClass:
    name: str
    extends: str = ""
    implements: list = field(default_factory=list)
    sobject_refs: set = field(default_factory=set)        # custom objects referenced
    class_refs: set = field(default_factory=set)          # other apex classes referenced
    kind: str = "class"                                   # async category: class|batch|schedulable; TODO: add abstract/interface if needed
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
    c = _child(el, tag)
    return c.text if c is not None and c.text is not None else ""


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
    # `implements` may carry generics (`Database.Batchable<sObject>`); capture `<>`
    # so the clause isn't truncated mid-interface, then strip each interface's
    # generic params.
    impl_m = re.search(r"\bimplements\s+([\w.,<>\s]+?)\s*\{", s)
    implements = [re.sub(r"<.*?>", "", i).strip()
                  for i in impl_m.group(1).split(",")] if impl_m else []
    implements = [i for i in implements if i]
    kind = "class"
    impl_join = " ".join(implements)
    if "Batchable" in impl_join:
        kind = "batch"  # TODO: direct-implements only — misses inherited/multiple async interfaces
    elif "Schedulable" in impl_join:
        kind = "schedulable"
    sobj = set(re.findall(r"\b(\w+__c)\b", s))                    # custom objects/fields
    sobj |= set(re.findall(r"\bFROM\s+(\w+)", s, re.I))          # SOQL targets
    return SFClass(name=name, extends=extends, implements=implements,
                   sobject_refs=sobj, kind=kind, source=raw)  # TODO(NOISE-1/2): __c also matches custom fields; system calls (String.valueOf, JSON.serialize) leak — add a system-namespace denylist


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
    flow.objects = {el.text for el in _iter_local(root, "object") if el.text}
    start = _child(root, "start")
    if start is not None:
        flow.trigger_object = _text(start, "object")
    # apex invoked from action calls
    for ac in _iter_local(root, "actionCalls"):
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
    """All text values of <tag> anywhere under root (matched by local name)."""
    return [el.text for el in _iter_local(root, tag) if el.text]


# Platform component namespaces on flexipages — never custom/managed components.
_FLEXIPAGE_BUILTIN_NS = frozenset({
    "flexipage", "force", "forcecommunity", "forceknowledge", "lightning",
    "lightningcommunity", "lightningsnapin", "native", "interop", "aura", "ui",
    "siteforce", "sfa", "wave", "cms",
})


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
    # custom LWC/Aura are referenced as "c:componentName"; managed-package
    # components as "ns:componentName" -> keep the namespace as ns__componentName.
    refs = set()
    for c in fp.components:
        ns, sep, comp = c.partition(":")
        if not sep or not ns or not comp:
            continue
        ns = ns.lower()
        if ns == "c":
            refs.add(comp)
        elif ns not in _FLEXIPAGE_BUILTIN_NS and not ns.startswith("runtime_"):
            refs.add(f"{ns}__{comp}")
    fp.lwc_refs = refs
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
    for op in _iter_local(root, "objectPermissions"):
        o = _text(op, "object")
        if o:
            acc.objects.add(o)
    for fp_ in _iter_local(root, "fieldPermissions"):
        f = _text(fp_, "field")
        if f:
            acc.fields.add(f)
    for ca in _iter_local(root, "classAccesses"):
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


