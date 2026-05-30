"""OmniStudio digest — Integration Procedures, OmniScripts, Data Mappers, FlexCards.

Handles the **standard** OmniStudio metadata format (confirmed against a real
trial org's metadata describe + a real FlexCard export) AND the older Vlocity
managed-package DataPacks:

  Standard (one XML-meta file per component, definition embedded as JSON):
    OmniScript            -> *.os-meta.xml
    Integration Procedure -> *.oip-meta.xml
    Data Mapper           -> *.rpt-meta.xml
    FlexCard              -> *.ouc-meta.xml
  The JSON lives in <propertySetConfig> (and <dataSourceConfig> for the data
  binding). We extract those fields, parse the JSON, and scan it for references.

  Vlocity (old model): *_DataPack.json with the definition as the JSON body.

The **file format and field layout are real-data-confirmed.** Element-level
reference *key names* (REF_KEYS) are still partly provisional — the trial org has
no scripts/IPs/Data Mappers *with* references to confirm them against, so those
will be tuned when a populated sample is available. Extraction is key-driven, so
that's a one-line change. Names may be standard, custom (__c), or packaged
(ns__Name__c); we resolve against known sets, never inferring type from shape.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass
class OmniComponent:
    name: str
    otype: str                       # omniscript | integrationprocedure | datamapper | flexcard
    subtype: str = ""
    model: str = ""                  # standard | vlocity
    ip_refs: set = field(default_factory=set)
    dm_refs: set = field(default_factory=set)
    apex_refs: set = field(default_factory=set)
    lwc_refs: set = field(default_factory=set)
    object_refs: set = field(default_factory=set)
    source: str = ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


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


def _component(name, otype, refs, model, source) -> OmniComponent:
    return OmniComponent(
        name=name, otype=otype, model=model,
        ip_refs=refs["ip"], dm_refs=refs["datamapper"], apex_refs=refs["apex"],
        lwc_refs=refs["lwc"], object_refs=refs["object"], source=source,
    )


def parse_standard_meta(path: Path, otype: str) -> OmniComponent:
    """Parse a standard OmniStudio *-meta.xml: pull every embedded-JSON field
    (<propertySetConfig>, <dataSourceConfig>, …), parse, and scan for refs."""
    src = path.read_text("utf-8", errors="replace")
    name = path.name
    for suf in (".os-meta.xml", ".oip-meta.xml", ".rpt-meta.xml", ".ouc-meta.xml"):
        if name.endswith(suf):
            name = name[: -len(suf)]
            break
    refs = {k: set() for k in REF_KEYS}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return _component(name, otype, refs, "standard", src)
    for el in root.iter():
        if _local(el.tag).lower() in _JSON_FIELDS and el.text and el.text.strip()[:1] in "{[":
            try:
                obj = json.loads(el.text)
            except json.JSONDecodeError:
                continue
            for kind, s in collect_refs(obj).items():
                refs[kind] |= s
    return _component(name, otype, refs, "standard", src)


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
    out: list = []

    # standard runtime: classify by file suffix
    for suffix, otype in SUFFIX_TYPE.items():
        for f in sorted(base.rglob(f"*.{suffix}-meta.xml")):
            try:
                out.append(parse_standard_meta(f, otype))
            except Exception:                       # pragma: no cover
                continue

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
