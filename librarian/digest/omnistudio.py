"""OmniStudio digest — Integration Procedures, OmniScripts, Data Mappers.

PROVISIONAL. Built against the documented OmniStudio shapes for BOTH:
  - the newer "standard" runtime (OmniProcess / OmniDataTransform metadata), and
  - the older Vlocity managed-package DataPacks (JSON, namespaced records).

No OmniStudio sample is available in this repo, so this is validated on
synthetic fixtures only. Reference extraction is **key-driven** (REF_KEYS below),
so tuning it to a real *sanitized* export is a one-line change rather than a
rewrite. Names may be standard, custom (__c), or packaged (ns__Name__c) — we
never infer type from shape; we resolve against known sets.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Reference keys scanned anywhere in an OmniStudio definition (lower-cased).
# Union of standard + Vlocity spellings; extend when a real export is seen.
REF_KEYS = {
    "ip":         {"integrationprocedurekey", "integrationproceduretype"},
    "datamapper": {"bundle", "dataraptorbundlename", "drbundlename",
                   "dataraptorinputbundle", "dataraptoroutputbundle"},
    "apex":       {"remoteclass"},
    "lwc":        {"lwcname", "lwccomponentname", "lwccomponentoverride"},
    "object":     {"objectname", "interfaceobjectname", "objectapiname",
                   "inputobjectname", "outputobjectname"},
}

# Folder names where OmniStudio metadata is retrieved (standard runtime).
STANDARD_DIRS = {"omniProcesses": None, "omniDataTransforms": "datamapper"}


@dataclass
class OmniComponent:
    name: str
    otype: str                       # omniscript | integrationprocedure | datamapper
    subtype: str = ""
    model: str = ""                  # standard | vlocity
    ip_refs: set = field(default_factory=set)
    dm_refs: set = field(default_factory=set)
    apex_refs: set = field(default_factory=set)
    lwc_refs: set = field(default_factory=set)
    object_refs: set = field(default_factory=set)
    source: str = ""


def _walk(obj):
    """Yield (lowercased_key, value) for every dict key in nested JSON."""
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


def component_from_definition(name, otype, definition, model="standard",
                              subtype="", source="") -> OmniComponent:
    refs = collect_refs(definition)
    return OmniComponent(
        name=name, otype=otype, subtype=subtype, model=model,
        ip_refs=refs["ip"], dm_refs=refs["datamapper"], apex_refs=refs["apex"],
        lwc_refs=refs["lwc"], object_refs=refs["object"], source=source,
    )


def _classify_omniprocess(definition) -> str:
    """OmniProcess covers both OmniScripts and Integration Procedures; the
    process type field distinguishes them (standard + vlocity spellings)."""
    for k, v in _walk(definition):
        if k in ("omniprocesstype", "type", "omniprocesssubtype") and isinstance(v, str):
            if "integration" in v.lower():
                return "integrationprocedure"
            if "omniscript" in v.lower() or "script" in v.lower():
                return "omniscript"
    return "omniscript"


def parse_omnistudio(base_dir) -> list:
    """Best-effort loader for both models. Returns [] cleanly when no OmniStudio
    metadata is present, so it never affects orgs without OmniStudio."""
    base = Path(base_dir)
    out: list = []

    # --- standard runtime: omniProcesses/ and omniDataTransforms/ ---
    for dirname, forced_type in STANDARD_DIRS.items():
        d = base / dirname
        if not d.is_dir():
            continue
        for jf in sorted(d.rglob("*.json")):
            try:
                definition = json.loads(jf.read_text("utf-8", errors="replace"))
            except (json.JSONDecodeError, OSError):
                continue
            name = jf.stem
            otype = forced_type or _classify_omniprocess(definition)
            out.append(component_from_definition(
                name, otype, definition, model="standard",
                source=jf.read_text("utf-8", errors="replace")))

    # --- Vlocity DataPacks: *_DataPack.json under any vlocity/ export dir ---
    for dp in sorted(base.rglob("*_DataPack.json")):
        try:
            definition = json.loads(dp.read_text("utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        name = definition.get("name") or dp.stem.replace("_DataPack", "")
        otype = _classify_omniprocess(definition)
        # DataРaptorBundle datapacks classify as datamapper
        vlo = json.dumps(definition).lower()
        if "dataraptor" in vlo and "omniscript" not in vlo and "integrationprocedure" not in vlo:
            otype = "datamapper"
        out.append(component_from_definition(
            name, otype, definition, model="vlocity",
            source=dp.read_text("utf-8", errors="replace")))

    return out
