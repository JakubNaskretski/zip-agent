"""Extracts classic page layouts and compact layouts (`*.layout-meta.xml`,
`*.compactLayout-meta.xml`).

A layout file is named ``<Object>-<Layout Name>.layout-meta.xml``; the object is
the segment before the FIRST dash (a layout name may itself contain dashes). Each
becomes a ``layout/<stem>`` node with:
  - `page-for` → object (the filename prefix),
  - `reads` → field (``Object.Field`` from layoutSections/Columns/Items/<field>),
  - `uses` → quickaction (platform- and quickAction list items, by action name).

Field names only — never field values.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..xmlutil import local_name as _local

_LAYOUT_SUFFIX = ".layout-meta.xml"
_COMPACT_SUFFIX = ".compactLayout-meta.xml"


def _stem(path: Path) -> str:
    """Filename without the layout/compactLayout meta suffix."""
    name = path.name
    if name.endswith(_LAYOUT_SUFFIX):
        return name[: -len(_LAYOUT_SUFFIX)]
    if name.endswith(_COMPACT_SUFFIX):
        return name[: -len(_COMPACT_SUFFIX)]
    return path.stem


def _object_of(stem: str) -> str:
    """The `<Object>` prefix before the FIRST dash of the filename stem."""
    return stem.split("-", 1)[0] if "-" in stem else stem


class LayoutExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        n = path.name
        return n.endswith(_LAYOUT_SUFFIX) or n.endswith(_COMPACT_SUFFIX)

    def extract(self, path: Path):
        stem = _stem(path)
        lid = f"layout/{stem}"
        nodes = [node(lid, "layout", stem)]
        edges = []

        # page-for -> object: derivable from the filename alone, so it survives
        # even when the XML body is malformed.
        obj = _object_of(stem)
        if obj:
            edges.append(raw_edge(lid, "page-for", "object", obj))

        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError, ValueError):
            return nodes, edges

        seen_fields: set[str] = set()
        seen_actions: set[str] = set()

        # reads -> field: every <field> under layoutSections/Columns/Items. Walk
        # the tree by local name and take only <field>; the text is a field name.
        for el in root.iter():
            if _local(el.tag) != "field":
                continue
            fname = (el.text or "").strip()
            if not fname or fname in seen_fields:
                continue
            seen_fields.add(fname)
            # qualify with the layout's object so it resolves to field/<Object>.<Field>
            qualified = f"{obj}.{fname}" if obj else fname
            edges.append(raw_edge(lid, "reads", "field", qualified))

        # uses -> quickaction : platformActionList items reference an <actionName>;
        # quickActionList items reference a <quickActionName>. Take the NAME only.
        for el in root.iter():
            tag = _local(el.tag)
            if tag not in ("actionName", "quickActionName"):
                continue
            aname = (el.text or "").strip()
            if not aname or aname in seen_actions:
                continue
            seen_actions.add(aname)
            edges.append(raw_edge(lid, "uses", "quickaction", aname))

        return nodes, edges


EXTRACTORS = [LayoutExtractor()]
