"""Extracts Lightning pages (`*.flexipage-meta.xml`).

Each becomes a `flexipage/<Name>` node with `page-for` to its object
(sobjectType) and `embeds` to each custom component it references (a `c:Name`
reference targets `lwc/Name`).
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
        nodes = [node(fid, "flexipage", fp.name)]
        edges = []
        if fp.sobject:
            edges.append(raw_edge(fid, "page-for", "object", fp.sobject))
        for lwc in sorted(fp.lwc_refs):
            if lwc:
                edges.append(raw_edge(fid, "embeds", "lwc", lwc))
        return nodes, edges


EXTRACTORS = [FlexiPageExtractor()]
