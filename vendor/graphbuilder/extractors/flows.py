"""Extracts Salesforce flows (`*.flow-meta.xml`).

The flow becomes a `flow/<Name>` node with `touches`→object and `calls`→apexclass
edges. Each flow element (decisions / assignments / record CRUD / screens /
subflows / actionCalls) becomes a `flowelement` node wired to the flow with
`contains`, plus:
  - `subflow` (flow→flow) from subflow elements,
  - `invocable` (apex actionCalls → apexmethod or apexclass),
  - `reads` (recordLookups) / `writes` (recordCreates/Updates/Deletes) → object.

Field-level edges parsed from each element's children:
  - recordLookups: `<queriedFields>` and filter `<field>` → `reads` field
    (`Object.Field`), object from the element's `<object>`,
  - recordCreates / recordUpdates: `<inputAssignments><field>` → `writes` field,
  - decisions: a rule condition `<leftValueReference>` of the form
    `Record.Field__c` / `$Record.Field__c` → `reads` field, object from the
    flow's record-trigger start `<object>`.

Also: a screen `<fields>` embedding an LWC (via `<extensionName>` /
`componentName`) → `embeds` lwc; email-type action calls → `uses` the action by
name; and a `trigger_type` attr (`record` / `platformevent` / `schedule`) plus a
`schedule` flag derived from the `<start>`.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..salesforce import _text, parse_flow
from ..xmlutil import child as _child, children as _children, iter_local as _iter_local

# Flow element tags that become `flowelement` nodes (all carry a <name>).
_ELEMENT_TAGS = (
    "decisions", "assignments", "screens", "subflows", "actionCalls",
    "recordCreates", "recordUpdates", "recordLookups", "recordDeletes",
    "dynamicChoiceSets",
)
# Field-bearing children of a <dynamicChoiceSets> (record- or picklist-sourced).
_CHOICE_FIELD_TAGS = ("displayField", "valueField", "sortField", "picklistField")
# Record-element tag -> data access verb against its <object>.
_RECORD_ACCESS = {
    "recordLookups": "reads",
    "recordCreates": "writes",
    "recordUpdates": "writes",
    "recordDeletes": "writes",
}

# leftValueReference prefixes that denote the record-triggered flow's record.
_RECORD_PREFIXES = ("$Record__Prior", "$Record", "Record")

# actionCall types that send an email -> `uses` edge (name only).
_EMAIL_ACTION_TYPES = frozenset({"emailAlert", "emailSimple"})


def _lwc_name(raw):
    """A screen-field extension/component reference -> LWC name, or None.

    Normalizes the namespace prefix. The default-namespace marker `c` (`c:Foo`,
    `c__Foo`) is dropped so the edge matches the local `lwc/<name>` node, while a
    real managed-package namespace is preserved in API form -- both `ns:Foo` and
    `ns__Foo` -> `ns__Foo` -- so the edge targets the packaged component instead of
    colliding with (or masquerading as) a local one. Anything that doesn't look
    like a single component token is skipped.
    """
    if not raw:
        return None
    name = raw.strip()
    if not name:
        return None
    # split off a `ns:` or `ns__` namespace prefix
    ns = ""
    if ":" in name:
        ns, name = name.rsplit(":", 1)
    elif "__" in name:
        ns, name = name.rsplit("__", 1)
    name = name.strip()
    # a clean component token: no whitespace/dots left over
    if not name or " " in name or "." in name:
        return None
    # default namespace (`c`/empty) -> bare local name; real namespace -> API form
    if ns and ns != "c":
        return f"{ns}__{name}"
    return name


def _trigger_attrs(start):
    """Derive (trigger_type, scheduled) from the flow's `<start>` element.

    - scheduled when a `<schedule>` child exists, the start `<triggerType>` is
      `Scheduled`, or a `<schedule...>` path/recordTriggerType marks a schedule;
    - `platformevent` when the start `<object>` ends with `__e`;
    - `record` when a record start is present (object and/or recordTriggerType);
    - otherwise no trigger_type (returns None).
    """
    if start is None:
        return None, False
    obj = _text(start, "object")
    trigger_type = _text(start, "triggerType")
    scheduled = bool(_findall(start, "schedule")) or trigger_type == "Scheduled"

    if scheduled:
        return "schedule", True
    if obj and obj.endswith("__e"):
        return "platformevent", False
    if obj or trigger_type or _text(start, "recordTriggerType"):
        return "record", False
    return None, False


def _findall(el, tag):
    """Direct children named <tag> (by local name); [] on anything odd."""
    try:
        return _children(el, tag)
    except Exception:
        return []


def _field_name(raw, obj):
    """Build a clean `Object.Field` name or None if it can't be formed safely."""
    if not raw or not obj:
        return None
    field = raw.strip()
    if not field or "." in field or " " in field:
        return None            # not a bare field token — skip
    return f"{obj}.{field}"


def _decision_field(ref, obj):
    """A leftValueReference `Record.Field__c` / `$Record.Field__c` -> `Object.Field`.

    Only single-hop record-field refs are taken; anything else (element merge
    fields, multi-hop relationship traversals, globals) is skipped.
    """
    if not ref or not obj:
        return None
    ref = ref.strip()
    for prefix in _RECORD_PREFIXES:
        if ref.startswith(prefix + "."):
            field = ref[len(prefix) + 1:]
            # single hop only: `Field__c`, not `Parent__r.Field__c`
            if field and "." not in field and " " not in field:
                return f"{obj}.{field}"
    return None


class FlowExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(".flow-meta.xml")

    def extract(self, path: Path):
        flow = parse_flow(path)
        fid = f"flow/{flow.name}"
        flow_node = node(fid, "flow", flow.name, process_type=flow.process_type)
        nodes = [flow_node]
        edges = []

        for obj in sorted(flow.objects):
            edges.append(raw_edge(fid, "touches", "object", obj))
        for cls in sorted(flow.class_refs):
            edges.append(raw_edge(fid, "calls", "apexclass", cls))

        # Element-level edges need the raw XML; skip them on a parse failure.
        try:
            root = ET.parse(path).getroot()
        except Exception:
            return nodes, edges

        # record-triggered start object — context for decision Record.Field refs
        start = _child(root, "start")
        trigger_obj = _text(start, "object") if start is not None else ""

        trigger_type, scheduled = _trigger_attrs(start)
        if trigger_type:
            flow_node["trigger_type"] = trigger_type
        if scheduled:
            flow_node["schedule"] = True

        for tag in _ELEMENT_TAGS:
            for el in _children(root, tag):
                ename = _text(el, "name")
                if not ename:
                    continue
                eid = f"flowelement/{flow.name}.{ename}"
                nodes.append(node(eid, "flowelement", ename,
                                  flow=flow.name, element_type=tag,
                                  flow_label=_text(el, "label") or ename))
                edges.append(raw_edge(fid, "contains", "flowelement", f"{flow.name}.{ename}"))

                # subflow: flow -> flow
                if tag == "subflows":
                    target = _text(el, "flowName")
                    if target:
                        edges.append(raw_edge(fid, "subflow", "flow", target))

                # apex action call: element/flow -> apexmethod or apexclass
                elif tag == "actionCalls":
                    action_type = _text(el, "actionType")
                    if action_type == "apex":
                        action = _text(el, "actionName")
                        if action:
                            to_kind = "apexmethod" if "." in action else "apexclass"
                            edges.append(raw_edge(eid, "invocable", to_kind, action))
                    # email actions -> uses the alert/template by name.
                    elif action_type in _EMAIL_ACTION_TYPES:
                        action = _text(el, "actionName")
                        if action:
                            edges.append(raw_edge(eid, "uses", "emailalert", action))

                # Screen field components that embed an LWC -> embeds. Scan every
                # descendant <fields> (they nest under sections / columns), so
                # component instances at any depth are caught.
                elif tag == "screens":
                    seen_lwc = set()
                    for fld in _iter_local(el, "fields"):
                        ref = _text(fld, "extensionName") or _text(fld, "componentName")
                        lwc = _lwc_name(ref)
                        if lwc and lwc not in seen_lwc:
                            seen_lwc.add(lwc)
                            edges.append(raw_edge(eid, "embeds", "lwc", lwc))

                # dynamic choice set: record/picklist-sourced choices read an
                # object and its display/value/sort fields.
                elif tag == "dynamicChoiceSets":
                    dobj = _text(el, "object") or _text(el, "picklistObject")
                    if dobj:
                        edges.append(raw_edge(eid, "reads", "object", dobj))
                        seen = set()
                        for ftag in _CHOICE_FIELD_TAGS:
                            fname = _field_name(_text(el, ftag), dobj)
                            if fname and fname not in seen:
                                seen.add(fname)
                                edges.append(raw_edge(eid, "reads", "field", fname))

                # decision: rule conditions reference record fields (reads)
                elif tag == "decisions":
                    seen = set()
                    for rule in _findall(el, "rules"):
                        for cond in _findall(rule, "conditions"):
                            ref = _text(cond, "leftValueReference")
                            fname = _decision_field(ref, trigger_obj)
                            if fname and fname not in seen:
                                seen.add(fname)
                                edges.append(raw_edge(eid, "reads", "field", fname))

                # record element: element -> object (reads/writes)
                elif tag in _RECORD_ACCESS:
                    obj = _text(el, "object")
                    if obj:
                        edges.append(raw_edge(eid, _RECORD_ACCESS[tag], "object", obj))

                    # ---- field-level fidelity (object provides the context) ---- #
                    if not obj:
                        continue

                    if tag == "recordLookups":
                        seen = set()
                        # explicitly queried fields
                        for qf in _findall(el, "queriedFields"):
                            fname = _field_name(qf.text, obj)
                            if fname and fname not in seen:
                                seen.add(fname)
                                edges.append(raw_edge(eid, "reads", "field", fname))
                        # fields used in the lookup's filter conditions
                        for flt in _findall(el, "filters"):
                            fname = _field_name(_text(flt, "field"), obj)
                            if fname and fname not in seen:
                                seen.add(fname)
                                edges.append(raw_edge(eid, "reads", "field", fname))

                    elif tag in ("recordCreates", "recordUpdates"):
                        seen = set()
                        for ia in _findall(el, "inputAssignments"):
                            fname = _field_name(_text(ia, "field"), obj)
                            if fname and fname not in seen:
                                seen.add(fname)
                                edges.append(raw_edge(eid, "writes", "field", fname))

        # Record-typed flow variables declare an object dependency via
        # <objectType>, even when no CRUD element names that object directly.
        for v in _children(root, "variables"):
            ot = _text(v, "objectType")
            if ot and ot not in flow.objects:   # avoid duplicating a touches edge
                edges.append(raw_edge(fid, "touches", "object", ot))

        return nodes, edges


EXTRACTORS = [FlowExtractor()]
