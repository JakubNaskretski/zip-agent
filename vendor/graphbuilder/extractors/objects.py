"""Custom objects, their fields, lookups, formulas, and validations.

Owns each object's `*.object-meta.xml` and deep-parses the sibling field,
validationRule, and recordType files. Emits:

  - an `object` node plus a `field` node (`field_of`) per field, with `lookup`
    (field -> object) for reference fields; a reference field also carries a
    `relationship` attr ("master-detail" | "lookup").
  - `validates` (object -> field) for fields named in a validation rule's
    errorConditionFormula — bare own fields and relationship hops
    `Parent__r.Field__c` (resolved best-effort to the related object's field).
  - `formula` (formula field -> field) for own-object fields and cross-object
    `X__r.Y__c` hops.
  - `reads` (rollup-summary field -> the child object's `<summarizedField>` and
    `<summaryForeignKey>`).
  - `references` for a dependent picklist's `<controllingField>`, a field's
    `<lookupFilter>` field/valueField references, and a picklist pinned to a
    `globalvalueset` via `<valueSetName>`.
  - record-type nodes plus `contains` (object -> recordtype).

The object node carries a `category` attr derived purely from API name /
structural shape: "platformevent" (`__e`), "custommetadata" (`__mdt`),
"bigobject" (`__b`), "externalobject" (`__x`), "customsetting" (a
`<customSettingsType>` element), "custom" (`__c`), else "standard". Custom
settings also end in `__c`, so the `<customSettingsType>` check precedes the
bare `__c` test.

Names and structural relationships only — field values, formulas, filter `<value>`
literals, picklist values, and record-type `<picklistValues>` are never read.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..salesforce import parse_object
from ..xmlutil import child as _child, children as _children

# field types that reference another object via <referenceTo>
_REF_TYPES = {"lookup": "lookup", "masterdetail": "master-detail", "hierarchy": "lookup"}

# A field token inside a formula: a bare API name (Field__c) or a relationship
# hop (Account.Name). We pull the *first* segment as the local field reference and
# also keep dotted custom fields. Standard noise (functions, TEXT, ISBLANK...) is
# filtered by intersecting against the object's own declared field set.
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:__[a-z])?")

# A relationship hop: one or more `Rel__r.`/`Rel.` segments ending in a field name,
# e.g. `Site__r.Name`, `Account.Owner.Email`, `Parent__r.Reading__c`. We key off
# the *first* relationship segment (which we can map to a related object via the
# object's own lookup fields) and the *last* segment (the actual field read).
_HOP = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*(?:__r)?)"        # first relationship segment
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*(?:__r)?)*"     # any intermediate hops
    r"\.([A-Za-z_][A-Za-z0-9_]*(?:__[a-z])?)\b"  # terminal field name
)


def _text(el, tag: str) -> str:
    c = _child(el, tag)  # TODO: could reuse xmlutil.child_text (differs only in not stripping)
    return c.text if c is not None and c.text is not None else ""


def _has_custom_settings_type(meta_path: Path) -> bool:  # TODO: only presence is used; the value (List/Hierarchy) is safe to read if needed
    """True if the object-meta.xml declares a `<customSettingsType>` element.

    Structural presence check only; the element's value is never read. Falls back
    to a tag scan when the XML won't parse."""
    try:
        root = ET.parse(meta_path).getroot()
    except (ET.ParseError, OSError):
        # broken XML: tag presence check, still value-blind
        try:
            return "<customSettingsType" in meta_path.read_text("utf-8", errors="replace")
        except OSError:
            return False
    return _child(root, "customSettingsType") is not None


def _classify(name: str, meta_path: Path) -> str:
    """Categorise an object from its API name / structural shape only.

    Order matters: custom settings carry the same `__c` suffix as ordinary custom
    objects, so the `<customSettingsType>` check must precede the bare `__c` test."""
    n = name or ""
    if n.endswith("__e"):
        return "platformevent"
    if n.endswith("__mdt"):
        return "custommetadata"
    if n.endswith("__b"):
        return "bigobject"
    if n.endswith("__x"):
        return "externalobject"
    if _has_custom_settings_type(meta_path):
        return "customsetting"
    if n.endswith("__c"):
        return "custom"
    return "standard"


def _relationship_target(seg: str, rel_to_object: dict) -> str:
    """Map a relationship segment (`Site__r`, `Account`) to its related object name.

    Custom relationship hops use the `__r` suffix derived from a `__c` lookup
    field; standard hops (e.g. `Account`) name the object directly. Returns "" when
    the related object can't be resolved."""
    if not seg:
        return ""
    if seg.endswith("__r"):
        return rel_to_object.get(seg, "")
    # a standard relationship segment names the related object directly
    return seg


def _hop_field_refs(formula: str, rel_to_object: dict) -> set:
    """Best-effort `(related_object, field)` pairs from relationship hops.

    Resolves the leading relationship segment to a related object so the terminal
    field can be named on the right object. Unresolvable hops are skipped."""
    if not formula:
        return set()
    out: set = set()
    try:
        matches = _HOP.findall(formula)
    except Exception:
        return out
    for first_seg, field_name in matches:
        if not field_name:
            continue
        target_obj = _relationship_target(first_seg, rel_to_object)
        if not target_obj:
            continue
        out.add((target_obj, field_name))
    return out


def _filter_field_ref(raw: str, obj_name: str) -> str:
    """Map a lookup-filter `<field>` / `<valueField>` reference to an `Object.Field`
    qualified name. Handles the field *name* an item filters on, never the
    `<value>` literal it compares against.

    Salesforce qualifies these as `$Source.Field`, `Object.Field`, or a relationship
    path `Rel.Sub.Field`. The leading segment drives it: `$Source`/`$RecordType`
    map to the current object; any other `$Global` (e.g. `$User`, `$Profile`) names
    a global the current object can't host a field on and is skipped. A plain
    `Object.Field` keeps its named object. Returns "" when not resolvable."""
    if not raw:
        return ""
    raw = raw.strip()
    if "." not in raw:
        # bare field name -> assume it lives on the current object
        return f"{obj_name}.{raw}" if raw else ""
    head, rest = raw.split(".", 1)
    field_seg = rest.rsplit(".", 1)[-1]   # terminal field name (drop intermediate hops)
    if not field_seg:
        return ""
    if head.startswith("$"):
        # $Source / $RecordType refer to the record being filtered = current object;
        # other globals ($User, $Profile, ...) are not fields on this object -> skip
        if head in ("$Source", "$RecordType"):
            return f"{obj_name}.{field_seg}"
        return ""
    # `Object.Field` (or `Rel.Field`) -> name the field on the leading object
    return f"{head}.{field_seg}"


def _lookup_filter_field_refs(froot, obj_name: str) -> list:
    """Best-effort `Object.Field` references named by a field's `<lookupFilter>`.

    Reads only the `<field>` and `<valueField>` elements of each `<filterItems>`
    (both name *fields*); the `<value>` element holds a data literal and is
    ignored. Returns a de-duplicated, order-preserving list."""
    out: list = []
    seen: set = set()
    try:
        lf = _child(froot, "lookupFilter")
        if lf is None:
            return out
        for item in _children(lf, "filterItems"):
            for tag in ("field", "valueField"):   # NOT "value" — that is data
                ref = _filter_field_ref(_text(item, tag), obj_name)
                if ref and "." in ref and ref not in seen:
                    seen.add(ref)
                    out.append(ref)
    except Exception:
        return out
    return out


def _formula_field_refs(formula: str, own_fields: set) -> set:
    """Names from `formula` that match one of the object's own declared fields.

    Intersecting against the declared field set keeps function names and
    cross-object relationship hops out of the result."""
    if not formula:
        return set()
    try:
        tokens = set(_TOKEN.findall(formula))
    except Exception:
        return set()
    return {t for t in tokens if t in own_fields}


class ObjectExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(".object-meta.xml")

    def extract(self, path: Path):
        obj_dir = path.parent
        obj = parse_object(obj_dir)
        oid = f"object/{obj.name}"
        category = _classify(obj.name, path)
        nodes = [node(oid, "object", obj.label or obj.name, category=category)]
        edges: list = []

        own_field_names: set = set()
        # relationship-name (`Site__r`) -> related object, so hops in formulas /
        # validation rules can name the terminal field on the right object.
        rel_to_object: dict = {}
        # ---- fields: nodes + field_of, plus lookup/master-detail relationships ----
        for f in obj.fields:
            if not f.name:
                continue
            qual = f"{obj.name}.{f.name}"
            fid = f"field/{qual}"
            own_field_names.add(f.name)
            attrs: dict = {"field_type": f.type}
            rel = _REF_TYPES.get((f.type or "").replace(" ", "").lower())
            if rel:
                attrs["relationship"] = rel
            # the field's own decomposed file, not the parent object-meta.xml,
            # is the base source an agent should be pointed at
            ffile = obj_dir / "fields" / f"{f.name}.field-meta.xml"
            if ffile.is_file():
                attrs["source_path"] = str(ffile)
            nodes.append(node(fid, "field", qual, **attrs))
            edges.append(raw_edge(fid, "field_of", "object", obj.name))
            # lookup edge field -> referenced object (master-detail or lookup)
            if rel and f.reference_to:
                edges.append(raw_edge(fid, "lookup", "object", f.reference_to))
                # default relationship name: `Site__c` -> `Site__r`
                if f.name.endswith("__c"):
                    rel_to_object[f.name[:-3] + "__r"] = f.reference_to

        # ---- deep: per-field XML (formula / rollup / explicit relationshipName) #
        fields_dir = obj_dir / "fields"
        if fields_dir.is_dir():
            # First pass: pick up any explicit <relationshipName> so hops resolve
            # even when the relationship name diverges from the field-name default.
            for fp in sorted(fields_dir.glob("*.field-meta.xml")):
                try:
                    froot = ET.parse(fp).getroot()
                except (ET.ParseError, OSError):
                    continue
                ref_to = _text(froot, "referenceTo")
                relname = _text(froot, "relationshipName")
                if ref_to and relname:
                    rel_to_object[relname] = ref_to
                    rel_to_object[relname + "__r"] = ref_to

            for fp in sorted(fields_dir.glob("*.field-meta.xml")):
                try:
                    froot = ET.parse(fp).getroot()
                except (ET.ParseError, OSError):
                    continue
                fname = _text(froot, "fullName") or fp.stem.replace(".field-meta", "")
                if not fname:
                    continue
                src_id = f"field/{obj.name}.{fname}"

                # --- formula fields -> formula edges (field -> field) --------- #
                formula = _text(froot, "formula")
                if formula:
                    # own-object field references
                    for ref in sorted(_formula_field_refs(formula, own_field_names) - {fname}):
                        edges.append(raw_edge(src_id, "formula", "field", f"{obj.name}.{ref}"))
                    # cross-object relationship hops `X__r.Y__c`
                    for tobj, tfield in sorted(_hop_field_refs(formula, rel_to_object)):
                        edges.append(raw_edge(src_id, "formula", "field", f"{tobj}.{tfield}"))

                # --- rollup-summary fields -> reads (child field + child FK) -- #
                summarized = _text(froot, "summarizedField")     # Child__c.Amount__c
                if summarized and "." in summarized:
                    child_obj = summarized.split(".", 1)[0]
                    edges.append(raw_edge(src_id, "reads", "field", summarized))
                    if child_obj:
                        edges.append(raw_edge(src_id, "reads", "object", child_obj))
                fkey = _text(froot, "summaryForeignKey")          # Child__c.Parent__c
                if fkey and "." in fkey:
                    edges.append(raw_edge(src_id, "reads", "field", fkey))

                # --- dependent picklist -> references its controlling field ---- #
                # `<valueSet><controllingField>Status__c</controllingField>` names
                # another field on THIS object that gates the available values.
                vset = _child(froot, "valueSet")
                ctrl = _text(vset, "controllingField") if vset is not None else ""
                if ctrl and ctrl != fname:
                    edges.append(raw_edge(src_id, "references", "field",
                                          f"{obj.name}.{ctrl}"))

                # --- picklist pinned to a Global Value Set -> references it ---- #
                # `<valueSet><valueSetName>Region</valueSetName>` shares a reusable
                # value list; the NAME is structural (the values are never read).
                gvs = _text(vset, "valueSetName") if vset is not None else ""
                if gvs:
                    edges.append(raw_edge(src_id, "references", "globalvalueset", gvs))

                # --- lookup filter -> references the fields it filters on ------ #
                # Only field NAMES (<field>/<valueField>); the <value> literal is
                # data and is never read.
                for ref in _lookup_filter_field_refs(froot, obj.name):
                    edges.append(raw_edge(src_id, "references", "field", ref))

        # ---- deep: validation rules -> validates edges (object -> field) ------ #
        vr_dir = obj_dir / "validationRules"
        if vr_dir.is_dir():
            for vp in sorted(vr_dir.glob("*.validationRule-meta.xml")):
                try:
                    vroot = ET.parse(vp).getroot()
                except (ET.ParseError, OSError):
                    continue
                cond = _text(vroot, "errorConditionFormula")
                # bare own-object fields
                for ref in sorted(_formula_field_refs(cond, own_field_names)):
                    edges.append(raw_edge(oid, "validates", "field", f"{obj.name}.{ref}"))
                # relationship hops `Parent__r.Field__c` -> validate the related field
                for tobj, tfield in sorted(_hop_field_refs(cond, rel_to_object)):
                    edges.append(raw_edge(oid, "validates", "field", f"{tobj}.{tfield}"))

        # ---- deep: record types -> node + contains (object -> recordtype) ----- #
        # Structural only: the record type's NAME, never its <picklistValues> data.
        rt_dir = obj_dir / "recordTypes"
        if rt_dir.is_dir():
            for rp in sorted(rt_dir.glob("*.recordType-meta.xml")):
                try:
                    rroot = ET.parse(rp).getroot()
                    rt_name = _text(rroot, "fullName")
                except (ET.ParseError, OSError):
                    rt_name = ""
                if not rt_name:
                    # fall back to the file stem; never let a broken file drop the RT
                    rt_name = rp.name[:-len(".recordType-meta.xml")] \
                        if rp.name.endswith(".recordType-meta.xml") else rp.stem
                if not rt_name:
                    continue
                rt_qual = f"{obj.name}.{rt_name}"
                rt_id = f"recordtype/{rt_qual}"
                nodes.append(node(rt_id, "recordtype", rt_qual,
                                  source_path=str(rp)))
                edges.append(raw_edge(oid, "contains", "recordtype", rt_qual))

        return nodes, edges


EXTRACTORS = [ObjectExtractor()]
