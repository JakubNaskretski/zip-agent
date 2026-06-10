"""OmniStudio parsers — Integration Procedures, OmniScripts, Data Mappers, FlexCards.

Handles two metadata formats:

  Standard — one XML-meta file per component, definition embedded as JSON:
    OmniScript            -> *.os-meta.xml
    Integration Procedure -> *.oip-meta.xml
    Data Mapper           -> *.rpt-meta.xml
    FlexCard              -> *.ouc-meta.xml
  The JSON lives in <propertySetConfig> (and <dataSourceConfig> for the data
  binding); it is parsed and scanned for references by known keys (REF_KEYS).

  Vlocity (legacy) — *_DataPack.json with the definition as the JSON body.

Names may be standard, custom (__c), or packaged (ns__Name__c); references resolve
against known node sets rather than inferring type from shape.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .xmlutil import local_name as _local

# Reference keys scanned anywhere in an OmniStudio definition (lower-cased).
REF_KEYS = {
    "ip":         {"integrationprocedurekey", "integrationproceduretype", "ipmethod"},
    "datamapper": {"bundle", "dataraptorbundlename", "drbundlename",
                   "dataraptorinputbundle", "dataraptoroutputbundle"},
    "apex":       {"remoteclass"},
    "lwc":        {"lwcname", "lwccomponentname", "lwccomponentoverride"},
    "object":     {"objectname", "interfaceobjectname", "objectapiname",
                   "inputobjectname", "outputobjectname", "contextobject"},
}

# standard-metadata file suffix -> component type
SUFFIX_TYPE = {
    "os": "omniscript", "oip": "integrationprocedure",
    "rpt": "datamapper", "ouc": "flexcard",
}
# XML fields that carry an embedded JSON definition
_JSON_FIELDS = {"propertysetconfig", "datasourceconfig", "propertysetconfigchunks"}
# structured XML element tags that directly hold a reference (Data Mappers use these)
_XML_REF_FIELDS = {"inputobjectname": "object", "outputobjectname": "object"}
# output "object" values that are really data formats, not SObjects
_OUTPUT_FORMATS = {"json", "xml", "csv", "custom", ""}


@dataclass
class OmniComponent:
    name: str                        # canonical key: Type_SubType (OS/IP) or Name (DM/card)
    otype: str                       # omniscript | integrationprocedure | datamapper | flexcard
    subtype: str = ""
    model: str = ""                  # standard | vlocity
    active: bool = True
    version: float = 0.0
    ip_refs: set = field(default_factory=set)
    dm_refs: set = field(default_factory=set)
    apex_refs: set = field(default_factory=set)
    lwc_refs: set = field(default_factory=set)
    object_refs: set = field(default_factory=set)
    source: str = ""


def _walk(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k).lower(), v
            yield from _walk(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def collect_refs(definition) -> dict:
    """Scan a parsed definition for reference values by known keys."""
    out = {kind: set() for kind in REF_KEYS}
    for k, v in _walk(definition):
        if isinstance(v, str) and v.strip():
            for kind, keys in REF_KEYS.items():
                if k in keys:
                    out[kind].add(v.strip())
    return out


def _component(name, otype, refs, model, source, active=True, version=0.0) -> OmniComponent:
    return OmniComponent(
        name=name, otype=otype, model=model, active=active, version=version,
        ip_refs=refs["ip"], dm_refs=refs["datamapper"], apex_refs=refs["apex"],
        lwc_refs=refs["lwc"], object_refs=refs["object"], source=source,
    )


def parse_standard_meta(path: Path, otype: str) -> OmniComponent:
    """Parse a standard OmniStudio *-meta.xml.

    References come from two places: embedded-JSON fields (<propertySetConfig> at
    script level AND inside each <omniProcessElements> child — found via recursive
    iter) for OmniScript/IP/FlexCard, and structured XML tags (inputObjectName,
    outputObjectName) for Data Mappers. The canonical name is `Type_SubType`
    (OmniScript/IP — how they're referenced) or `Name` (Data Mapper/FlexCard);
    the filename carries a version suffix we must not use for resolution.
    """
    src = path.read_text("utf-8", errors="replace")
    filestem = path.name
    for suf in (".os-meta.xml", ".oip-meta.xml", ".rpt-meta.xml", ".ouc-meta.xml"):
        if filestem.endswith(suf):
            filestem = filestem[: -len(suf)]
            break
    refs = {k: set() for k in REF_KEYS}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return _component(filestem, otype, refs, "standard", src)

    top = {}
    for e in root:
        ln = _local(e.tag)
        if ln not in top:                       # first occurrence of simple fields
            top[ln] = e.text or ""

    # canonical name
    typ, sub, nm = top.get("type", ""), top.get("subType", ""), top.get("name", "")
    if otype in ("omniscript", "integrationprocedure") and typ and sub:
        cname = f"{typ}_{sub}"
    else:
        cname = nm or filestem

    active = top.get("isActive", "true").strip().lower() != "false"
    try:
        version = float(top.get("versionNumber", "0") or 0)
    except ValueError:
        version = 0.0

    for el in root.iter():
        ln = _local(el.tag).lower()
        txt = el.text or ""
        if ln in _JSON_FIELDS and txt.strip()[:1] in "{[":
            try:
                obj = json.loads(txt)
            except json.JSONDecodeError:
                continue
            for kind, s in collect_refs(obj).items():
                refs[kind] |= s
        elif ln in _XML_REF_FIELDS and txt.strip() and txt.strip().lower() not in _OUTPUT_FORMATS:
            refs[_XML_REF_FIELDS[ln]].add(txt.strip())

    return _component(cname, otype, refs, "standard", src, active=active, version=version)


def _classify_vlocity(definition) -> str:
    blob = json.dumps(definition).lower()
    for k, v in _walk(definition):
        if k in ("omniprocesstype", "type", "vlocityrecordsobjecttype") and isinstance(v, str):
            vl = v.lower()
            if "integration" in vl:
                return "integrationprocedure"
            if "dataraptor" in vl or "datamapper" in vl:
                return "datamapper"
            if "omniscript" in vl or "script" in vl:
                return "omniscript"
    if "dataraptor" in blob and "omniscript" not in blob:
        return "datamapper"
    return "omniscript"


def parse_omnistudio(base_dir) -> list:
    """Parse standard OmniStudio metadata + Vlocity DataPacks. Returns [] cleanly
    when none is present, so plain orgs are unaffected."""
    base = Path(base_dir)

    # standard runtime: classify by file suffix
    standard = []
    for suffix, otype in SUFFIX_TYPE.items():
        for f in sorted(base.rglob(f"*.{suffix}-meta.xml")):
            try:
                standard.append(parse_standard_meta(f, otype))
            except Exception:                       # pragma: no cover
                continue
    # one component per (otype, canonical name): keep the active version, else the
    # highest versionNumber (OmniScripts/IPs have multiple versions on disk)
    best: dict = {}
    for c in standard:
        key = (c.otype, c.name)
        cur = best.get(key)
        if cur is None or (c.active, c.version) > (cur.active, cur.version):
            best[key] = c
    out: list = list(best.values())

    # Vlocity DataPacks (old model)
    for dp in sorted(base.rglob("*_DataPack.json")):
        try:
            definition = json.loads(dp.read_text("utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        name = definition.get("name") or dp.stem.replace("_DataPack", "")
        src = dp.read_text("utf-8", errors="replace")
        out.append(_component(name, _classify_vlocity(definition),
                              collect_refs(definition), "vlocity", src))

    return out
