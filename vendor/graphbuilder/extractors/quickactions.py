"""Quick Actions — `*.quickAction-meta.xml`.

Names and structure only — never values, labels, or body text. Emits:
  - node `quickaction/<Name>`; `Name` is the filename stem, which may be
    object-specific (`Object.ActionName`) or global (`GlobalActionName`).
  - `on` -> object: the explicit `<targetObject>` if present, else the object
    context implied by an `Object.ActionName` filename.
  - `<lightningComponent>` -> `embeds` -> lwc.
  - `<page>` -> `embeds` -> vfpage.
  - `<flowDefinition>` -> `calls` -> flow.
  - `quickActionLayout` fields (`<field>` under the layout) -> `reads` -> field
    (qualified `Object.Field` when an object context is known).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..xmlutil import child as _child, iter_local as _iter_local


def _first_text(root, tag: str) -> str:
    """Text of the first top-level <tag> child (by local name), stripped."""
    el = _child(root, tag)
    if el is not None and el.text:
        return el.text.strip()
    return ""


def _names_from_stem(name: str) -> tuple[str, str]:
    """Split a quick-action filename stem into (object_context, action).

    Object-specific actions are named `Object.ActionName`; global actions are a
    bare name with no dot. The object segment is the context the action runs on.
    A namespaced/packaged name like `ns__Obj__c.Action` keeps the full object
    segment (everything before the LAST dot is the object reference).
    """
    if "." in name:
        obj, _, action = name.rpartition(".")
        return obj, action
    return "", name


def _iter_layout_fields(root):
    """Yield bare field API names referenced anywhere under a quickActionLayout.

    The layout nests as quickActionLayout > quickActionLayoutColumns >
    quickActionLayoutItems > field. We scan for any <field> element under a
    <quickActionLayout> subtree so structural variations don't drop refs. Only
    the field NAME is read — never any value, label, or default.
    """
    for layout in _iter_local(root, "quickActionLayout"):
        for fel in _iter_local(layout, "field"):
            if fel.text and fel.text.strip():
                yield fel.text.strip()


class QuickActionExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(".quickAction-meta.xml")

    def extract(self, path: Path):
        name = path.name[: -len(".quickAction-meta.xml")] or path.stem
        obj_ctx, _action = _names_from_stem(name)

        qid = f"quickaction/{name}"
        nodes = [node(qid, "quickaction", name)]
        edges: list[dict] = []

        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            return nodes, edges
        if root is None:
            return nodes, edges

        # --- object context: explicit <targetObject> wins, else the filename's
        #     object segment (object-specific actions encode it in the name) ---
        try:
            target_object = _first_text(root, "targetObject")
        except Exception:
            target_object = ""
        on_object = target_object or obj_ctx
        if on_object:
            edges.append(raw_edge(qid, "on", "object", on_object))

        qa_type = ""
        try:
            qa_type = _first_text(root, "type")
        except Exception:
            qa_type = ""

        # --- typed targets (presence of the element is what matters; the
        #     declared <type> is recorded as a node attr for context) ---------
        try:
            lwc_name = _first_text(root, "lightningComponent")
        except Exception:
            lwc_name = ""
        if lwc_name:
            edges.append(raw_edge(qid, "embeds", "lwc", lwc_name))

        try:
            page = _first_text(root, "page")
        except Exception:
            page = ""
        if page:
            edges.append(raw_edge(qid, "embeds", "vfpage", page))

        try:
            flow = _first_text(root, "flowDefinition")
        except Exception:
            flow = ""
        if flow:
            edges.append(raw_edge(qid, "calls", "flow", flow))

        if qa_type:
            nodes[0]["action_type"] = qa_type

        # --- quickActionLayout fields -> reads -> field ----------------------
        seen_fields: set[str] = set()
        try:
            layout_fields = list(_iter_layout_fields(root))
        except Exception:
            layout_fields = []
        for fname in layout_fields:
            # Qualify against the object context when one is known so the field
            # node id matches the `field/<Object>.<Field>` convention; an
            # already-qualified ref (Object.Field) is left as-is.
            qualified = fname if "." in fname else (
                f"{on_object}.{fname}" if on_object else fname
            )
            if qualified in seen_fields:
                continue
            seen_fields.add(qualified)
            edges.append(raw_edge(qid, "reads", "field", qualified))

        return nodes, edges


EXTRACTORS = [QuickActionExtractor()]
