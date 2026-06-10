"""Extracts CustomApplication (`*.app-meta.xml`) and CustomTab (`*.tab-meta.xml`).

An app becomes `app/<Name>` (Name from the filename) with a `contains` edge to
each tab it lists. A tab becomes `tab/<Name>` with one classifying edge:
  - custom-object tab (`<sobjectType>`, or name ending `__c`) -> `references` -> object
  - lwc/aura component tab (`<lwcComponent>`/`<auraComponent>`) -> `embeds` -> lwc
  - flexipage tab (`<flexiPage>`)                              -> `page-for` -> flexipage

Names and structure only — never label text, URLs, or other values.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..xmlutil import local_name as _local, child_text as _child_text

_APP_SUFFIX = ".app-meta.xml"
_TAB_SUFFIX = ".tab-meta.xml"


def _root(path: Path):
    """Parse the file; return the root element or None on any parse error."""
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None


def _all_text(root, tag: str):
    """All <tag> text values anywhere under root (namespaced + bare), trimmed."""
    out = []
    for el in root.iter():
        if _local(el.tag) == tag and el.text and el.text.strip():
            out.append(el.text.strip())
    return out


class AppTabExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(_APP_SUFFIX) or path.name.endswith(_TAB_SUFFIX)

    def extract(self, path: Path):
        if path.name.endswith(_APP_SUFFIX):
            return self._extract_app(path)
        if path.name.endswith(_TAB_SUFFIX):
            return self._extract_tab(path)
        return [], []

    # --- CustomApplication: app/<Name> + contains -> tab for each <tabs> -------
    def _extract_app(self, path: Path):
        name = path.name[: -len(_APP_SUFFIX)]
        aid = f"app/{name}"
        nodes = [node(aid, "app", name)]
        edges = []
        root = _root(path)
        if root is None:
            return nodes, edges
        seen: set = set()
        for tab in _all_text(root, "tabs"):
            if tab and tab not in seen:
                seen.add(tab)
                edges.append(raw_edge(aid, "contains", "tab", tab))
        return nodes, edges

    # --- CustomTab: tab/<Name> + one classifying edge ------------------------
    def _extract_tab(self, path: Path):
        name = path.name[: -len(_TAB_SUFFIX)]
        tid = f"tab/{name}"
        nodes = [node(tid, "tab", name)]
        edges = []
        root = _root(path)
        if root is None:
            return nodes, edges

        sobject = _child_text(root, "sobjectType")
        lwc = _child_text(root, "lwcComponent")
        aura = _child_text(root, "auraComponent")
        flexipage = _child_text(root, "flexiPage")

        # custom-object tab: explicit <sobjectType>, or the tab name is the
        # object API name (any custom suffix: __c/__e/__mdt/__x/__b, incl. the
        # __x external-object tabs that packaged orgs commonly expose).
        if sobject:
            edges.append(raw_edge(tid, "references", "object", sobject))
        elif name.lower().endswith(("__c", "__e", "__mdt", "__x", "__b")):
            edges.append(raw_edge(tid, "references", "object", name))
        # lwc / aura component tab
        elif lwc:
            edges.append(raw_edge(tid, "embeds", "lwc", lwc))
        elif aura:
            edges.append(raw_edge(tid, "embeds", "lwc", aura))
        # flexipage tab
        elif flexipage:
            edges.append(raw_edge(tid, "page-for", "flexipage", flexipage))

        return nodes, edges


EXTRACTORS = [AppTabExtractor()]
