"""Extracts Lightning pages (`*.flexipage-meta.xml`).

Each becomes a `flexipage/<Name>` node with `page-for` to its object
(sobjectType), `embeds` to each custom component it references (a `c:Name`
reference targets `lwc/Name`), `reads` to every field the page shows or its
visibility rules test (qualified against the page's object — pages without an
sobjectType emit no field edges), and `uses` to the QuickActions it surfaces
(named in quickaction file-stem form so they resolve to real nodes).

Node attrs: `page_type` (RecordPage/AppPage/HomePage/...), `template`,
`master_label`, and `related_lists` (the related-list API names shown — kept as
an attr because a relationship name does not identify the related object, so
there is no safe edge target; see parse_flexipage for the full skip rationale).
"""
from __future__ import annotations

from pathlib import Path

from ..core import node, raw_edge
from ..salesforce import parse_flexipage


class FlexiPageExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(".flexipage-meta.xml")

    def extract(self, path: Path):
        fp = parse_flexipage(path)
        fid = f"flexipage/{fp.name}"
        n = node(fid, "flexipage", fp.name)
        if fp.page_type:
            n["page_type"] = fp.page_type
        if fp.template:
            n["template"] = fp.template
        if fp.master_label:
            n["master_label"] = fp.master_label
        if fp.related_lists:
            n["related_lists"] = fp.related_lists
        nodes = [n]
        edges = []
        if fp.sobject:
            edges.append(raw_edge(fid, "page-for", "object", fp.sobject))
        for lwc in sorted(fp.lwc_refs):
            if lwc:
                edges.append(raw_edge(fid, "embeds", "lwc", lwc))
        # Bare field names qualify against the page's object; without an
        # sobjectType (App/Home pages) they are dropped — an unqualified
        # `field/.Name` id would be garbage. Related-list parentFieldApiName
        # refs arrive already qualified, so they are safe either way.
        fields = set(fp.qualified_field_refs)
        if fp.sobject:
            fields |= {f"{fp.sobject}.{f}" for f in fp.field_refs}
        for f in sorted(fields):
            edges.append(raw_edge(fid, "reads", "field", f))
        for action in sorted(fp.action_refs):
            edges.append(raw_edge(fid, "uses", "quickaction", action))
        return nodes, edges


EXTRACTORS = [FlexiPageExtractor()]
