"""OmniStudio — OmniScripts, Integration Procedures, Data Mappers, FlexCards.

Owns the standard OmniStudio `*-meta.xml` files (by suffix) and the older Vlocity
`*_DataPack.json` exports. Emits one component node per canonical `Type_SubType`
plus a `flowelement/<Component>.<Element>` node per element (`contains` from the
component).

An OmniScript/IP has many version files on disk (one per `versionNumber`); only
the active — or, failing that, highest-versioned — component emits, chosen by
scanning the sibling `*-meta.xml` files, so dispatch order doesn't matter.

Per-element typed edges are resolved from each element's own JSON: Remote/Apex
actions -> `calls` -> apexclass; Integration Procedure actions -> `calls` ->
integrationprocedure; DataRaptor actions -> `uses` -> datamapper; FlexCard
actions/events naming a child component -> `embeds`. Data-mapper field mappings
yield `maps` -> field (`Object.Field`) and -> object.

Edges are de-duplicated across the two layers: a ref configured inside an element
is emitted only on that flowelement, never repeated on the component (which still
reaches it via `contains -> flowelement`). Refs belonging to the component's own
script/top-level config — not to any element — stay on the component. Every
relationship appears exactly once, at its true source.

Names and structural relationships only — never field values, formulas, labels,
endpoints, or any configuration payload.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from .. import omnistudio as om

# standard-metadata suffix -> component type (os/oip/rpt/ouc)
_SUFFIX_TYPE = om.SUFFIX_TYPE

# JSON fields (lower-cased) on an element that carry an embedded JSON definition.
_JSON_FIELDS = om._JSON_FIELDS

# Reference key sets, by target kind.
_IP_KEYS = om.REF_KEYS["ip"]
_DM_KEYS = om.REF_KEYS["datamapper"]
_APEX_KEYS = om.REF_KEYS["apex"]
_LWC_KEYS = om.REF_KEYS["lwc"]
_OBJ_KEYS = om.REF_KEYS["object"]

# Element-JSON keys (lower-cased) that name a target *component* a FlexCard /
# OmniScript element embeds (a card flips to another card, or shows a child card).
_CARD_TARGET_KEYS = {
    "cardname", "childcardname", "targetcardname", "flexcardname",
    "card", "targetcard", "childcard",
}

# data formats that are never real SObjects.
_OUTPUT_FORMATS = om._OUTPUT_FORMATS


def _suffix_otype(path: Path):
    """Return the OmniStudio otype for a standard *-meta.xml path, else None."""
    for suffix, otype in _SUFFIX_TYPE.items():
        if path.name.endswith(f".{suffix}-meta.xml"):
            return otype
    return None


def _clean(value):
    """A stripped non-empty string, or None."""
    if isinstance(value, str):
        s = value.strip()
        if s:
            return s
    return None


def _looks_like_field(name: str) -> bool:
    """`Object.Field` (exactly two dotted segments, both non-empty, no spaces)."""
    if not name or " " in name:
        return False
    parts = name.split(".")
    return len(parts) == 2 and all(parts)


class OmniStudioExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        try:
            name = path.name
        except Exception:
            return False
        if _suffix_otype(path) is not None:
            return True
        return name.endswith("_DataPack.json")

    def extract(self, path: Path):
        try:
            if path.name.endswith("_DataPack.json"):
                comp = self._parse_datapack(path)
                element_blobs = self._datapack_elements(path)
            else:
                comp = self._best_version(path)
                element_blobs = self._meta_elements(path)
        except Exception:
            return [], []
        if comp is None:                       # this file lost the version race
            return [], []
        # Element layer first: each per-element ref is emitted at its precise source
        # (the flowelement), then excluded at the component level below so it is not
        # duplicated. Component-level refs belonging to no element stay on the
        # component.
        enodes, eedges = [], []
        try:
            enodes, eedges = self._emit_elements(comp, element_blobs)
        except Exception:                      # element layer must never break the base
            enodes, eedges = [], []
        elem_rels = {(e["type"], e["to_kind"], e["to_name"])
                     for e in eedges if e["type"] != "contains"}
        nodes, edges = self._emit(comp, exclude=elem_rels)
        nodes.extend(enodes)
        edges.extend(eedges)
        return nodes, edges

    # --- version selection ------------------------------------------------- #
    def _best_version(self, path: Path):
        """Parse `path`; if a sibling version of the same canonical component wins
        (active, then highest versionNumber), return None so only the winner emits."""
        otype = _suffix_otype(path)
        if otype is None:
            return None
        this = om.parse_standard_meta(path, otype)

        suffix = next(s for s, t in _SUFFIX_TYPE.items() if t == otype)
        winner, winner_path = this, path
        for sib in sorted(path.parent.glob(f"*.{suffix}-meta.xml")):
            if sib == path:
                continue
            try:
                other = om.parse_standard_meta(sib, otype)
            except Exception:
                continue
            if other.name != this.name:        # different canonical component
                continue
            if (other.active, other.version) > (winner.active, winner.version):
                winner, winner_path = other, sib
        # tie-break deterministically on path so exactly one file emits the winner
        if winner_path != path:
            return None
        return this

    def _parse_datapack(self, path: Path):
        try:
            definition = json.loads(path.read_text("utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            return None
        name = (definition.get("name") if isinstance(definition, dict) else None) \
            or path.stem.replace("_DataPack", "")
        return om._component(name, om._classify_vlocity(definition),
                             om.collect_refs(definition), "vlocity", "")

    # --- element discovery -------------------------------------------------- #
    def _meta_elements(self, path: Path):
        """Yield (element_name, element_type, parsed_json_blobs) for each
        `omniProcessElements` child of a standard *-meta.xml. The blobs are the
        element's own embedded-JSON definitions (propertySetConfig &c.)."""
        out = []
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            return out
        for el in root.iter():
            if om._local(el.tag).lower() != "omniprocesselements":
                continue
            name = etype = None
            blobs = []
            for child in el.iter():
                ln = om._local(child.tag).lower()
                txt = child.text or ""
                if ln == "name" and name is None:
                    name = _clean(txt)
                elif ln == "type" and etype is None:
                    etype = _clean(txt)
                elif ln in _JSON_FIELDS and txt.strip()[:1] in "{[":
                    try:
                        blobs.append(json.loads(txt))
                    except json.JSONDecodeError:
                        continue
            if name:
                out.append((name, etype or "", blobs))
        return out

    def _datapack_elements(self, path: Path):
        """Yield (element_name, element_type, [blob]) for each item in a Vlocity
        DataPack's element list (`items`/`elements`/`childItems`)."""
        out = []
        try:
            definition = json.loads(path.read_text("utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            return out
        if not isinstance(definition, dict):
            return out
        for key in ("items", "elements", "childItems"):
            seq = definition.get(key)
            if not isinstance(seq, list):
                continue
            for item in seq:
                if not isinstance(item, dict):
                    continue
                name = _clean(item.get("name")) or _clean(item.get("Name"))
                etype = _clean(item.get("type")) or _clean(item.get("Type")) \
                    or _clean(item.get("eleType")) or ""
                if name:
                    out.append((name, etype, [item]))
        return out

    # --- emit (component level) -------------------------------------------- #
    def _emit(self, comp, exclude=frozenset()):
        oid = f"{comp.otype}/{comp.name}"
        nodes = [node(oid, comp.otype, comp.name,
                      model=comp.model, active=comp.active, version=comp.version)]
        edges = []

        def add(refs, etype, to_kind):
            for ref in sorted(refs):
                if isinstance(ref, str) and ref.strip():
                    r = ref.strip()
                    # skip relationships the element layer already emits on a
                    # flowelement, so they aren't repeated on the component.
                    if (etype, to_kind, r) in exclude:
                        continue
                    edges.append(raw_edge(oid, etype, to_kind, r))

        add(comp.ip_refs, "calls", "integrationprocedure")
        add(comp.dm_refs, "uses", "datamapper")
        add(comp.apex_refs, "calls", "apexclass")
        add(comp.lwc_refs, "embeds", "lwc")
        obj_edge = "maps" if comp.otype == "datamapper" else "touches"
        add(comp.object_refs, obj_edge, "object")
        return nodes, edges

    # --- emit (element level) ---------------------------------------------- #
    def _emit_elements(self, comp, element_blobs):
        """One `flowelement/<Component>.<Element>` node per element (contained by
        the component) plus typed per-element edges sourced from that element."""
        oid = f"{comp.otype}/{comp.name}"
        nodes = []
        edges = []
        seen_elem = set()

        for ename, etype, blobs in element_blobs:
            ename = _clean(ename)
            if not ename:
                continue
            eid = f"flowelement/{comp.name}.{ename}"
            if eid in seen_elem:
                continue
            seen_elem.add(eid)
            nodes.append(node(eid, "flowelement", ename, element_type=etype or ""))
            edges.append(raw_edge(oid, "contains", "flowelement", f"{comp.name}.{ename}"))

            refs = self._element_refs(blobs)
            for name in sorted(refs["apex"]):
                edges.append(raw_edge(eid, "calls", "apexclass", name))
            for name in sorted(refs["ip"]):
                edges.append(raw_edge(eid, "calls", "integrationprocedure", name))
            for name in sorted(refs["datamapper"]):
                edges.append(raw_edge(eid, "uses", "datamapper", name))
            for name in sorted(refs["lwc"]):
                edges.append(raw_edge(eid, "embeds", "lwc", name))
            for name in sorted(refs["card"]):
                edges.append(raw_edge(eid, "embeds", "flexcard", name))

            # data-mapper field mappings: maps -> field (Object.Field) and -> object
            if comp.otype == "datamapper":
                fields, objects = self._mapping_targets(blobs)
                for fname in sorted(fields):
                    edges.append(raw_edge(eid, "maps", "field", fname))
                for obj in sorted(objects):
                    edges.append(raw_edge(eid, "maps", "object", obj))

        return nodes, edges

    def _element_refs(self, blobs):
        """Collect per-element reference names, keyed by target kind, from an
        element's embedded JSON. Keys come from REF_KEYS plus the card-target keys
        for FlexCard navigate/flip actions."""
        out = {"apex": set(), "ip": set(), "datamapper": set(),
               "lwc": set(), "card": set()}
        for blob in blobs:
            for k, v in om._walk(blob):
                val = _clean(v)
                if not val:
                    continue
                if k in _APEX_KEYS:
                    out["apex"].add(val)
                elif k in _IP_KEYS:
                    out["ip"].add(val)
                elif k in _DM_KEYS:
                    out["datamapper"].add(val)
                elif k in _LWC_KEYS:
                    out["lwc"].add(val)
                elif k in _CARD_TARGET_KEYS:
                    out["card"].add(val)
        return out

    def _mapping_targets(self, blobs):
        """From a data-mapper element's JSON, collect mapping target NAMES only:
        `Object.Field` strings (-> field) and their owning objects (-> object),
        plus any object named by an object key. Values are never read."""
        fields = set()
        objects = set()
        for blob in blobs:
            for k, v in om._walk(blob):
                val = _clean(v)
                if not val:
                    continue
                if k in _OBJ_KEYS and val.lower() not in _OUTPUT_FORMATS:
                    objects.add(val)
                # mapping target field names: keys that name a destination field
                elif k in ("outputfieldname", "targetfieldname", "fieldname",
                           "domainobjectfieldapiname", "vlocitydatafieldname") \
                        and _looks_like_field(val):
                    fields.add(val)
                    objects.add(val.split(".", 1)[0])
        return fields, objects


EXTRACTORS = [OmniStudioExtractor()]
