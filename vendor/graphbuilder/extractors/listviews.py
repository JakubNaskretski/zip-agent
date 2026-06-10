"""Extracts list views (`objects/<Object>/listViews/*.listView-meta.xml`).

Each becomes a `listview/<Object>.<Name>` node with `references` to its owning
object (from the folder path) and `reads` to each field named by a `<columns>`
entry or a filter's `<field>` (qualified to the owning object, or kept
cross-object for a `Rel.Field` hop). A filter's `<value>` is a data literal and
is never read.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..xmlutil import local_name as _local

_SUFFIX = ".listView-meta.xml"
# A field API-name shape: a name, optionally a `__c` custom suffix, optionally one
# relationship hop (`Account.Name`). Bare ALL-CAPS tokens are excluded below.
_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:__c)?(?:\.[A-Za-z][A-Za-z0-9_]*(?:__c)?)?$")


def _all_local(root, tag: str):
    """All elements anywhere under root whose local name is `tag`."""
    return [el for el in root.iter() if _local(el.tag) == tag]


def _object_name(path: Path) -> str:
    """Owning object = the grandparent folder (`objects/<Obj>/listViews/x`)."""
    parent = path.parent
    if _local(parent.name) == "listViews" or parent.name == "listViews":
        return parent.parent.name
    return ""


def _field_ref(token: str, obj_name: str) -> str:
    """Map a column/filter field token to a qualified `Object.Field`, or "" to skip.

    Skips ALL-CAPS pseudo-columns (`NAME`, `CREATED_DATE`) which name layout
    tokens rather than field APIs. A `Rel.Field` hop keeps its leading object."""
    token = (token or "").strip()
    if not token or not _FIELD.match(token):
        return ""
    if token.isupper():                 # NAME, RECORDTYPE, CREATED_DATE, ...
        return ""
    if "." in token:                    # `Account.Name` -> name field on Account
        return token
    return f"{obj_name}.{token}" if obj_name else ""


class ListViewExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(_SUFFIX)

    def extract(self, path: Path):
        name = path.name[: -len(_SUFFIX)]
        if not name:
            return [], []
        obj_name = _object_name(path)
        lid = f"listview/{obj_name}.{name}" if obj_name else f"listview/{name}"
        nodes = [node(lid, "listview", f"{obj_name}.{name}" if obj_name else name)]
        edges: list = []
        if obj_name:
            edges.append(raw_edge(lid, "references", "object", obj_name))

        root = None
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            root = None
        if root is None:
            return nodes, edges

        seen: set = set()

        def _read_field(token: str):
            ref = _field_ref(token, obj_name)
            if ref and ref not in seen:
                seen.add(ref)
                edges.append(raw_edge(lid, "reads", "field", ref))

        for col in _all_local(root, "columns"):
            _read_field(col.text or "")
        # Filter <field> names a field too; <value>/<operation> are skipped.
        for filt in _all_local(root, "filters"):
            for child in filt:
                if _local(child.tag) == "field":
                    _read_field(child.text or "")

        return nodes, edges


EXTRACTORS = [ListViewExtractor()]
