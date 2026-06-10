"""Extracts custom labels from the `CustomLabels` document (`*.labels-meta.xml`).

Each `<labels>` entry becomes a `label/<fullName>` node carrying only structural
attrs — `category` (from `<categories>`) and `language` — with no outgoing edges.
A label's `<value>` is the displayed text and is never read; only the name and
its category/language leave this extractor.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node
from ..xmlutil import local_name as _local, child_text as _child_text


class LabelExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(".labels-meta.xml")

    def extract(self, path: Path):
        nodes: list[dict] = []
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            return nodes, []

        seen: set[str] = set()
        # Each <labels> entry is a child of the <CustomLabels> root.
        for entry in root:
            if _local(entry.tag) != "labels":
                continue
            full_name = _child_text(entry, "fullName")
            if not full_name or full_name in seen:
                continue
            seen.add(full_name)

            attrs: dict = {}
            category = _child_text(entry, "categories")
            if category:
                attrs["category"] = category
            language = _child_text(entry, "language")
            if language:
                attrs["language"] = language
            # <value> is intentionally never read or emitted.

            nodes.append(node(f"label/{full_name}", "label", full_name, **attrs))

        return nodes, []


EXTRACTORS = [LabelExtractor()]
