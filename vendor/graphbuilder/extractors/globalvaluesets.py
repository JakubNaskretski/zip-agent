"""Extracts global value sets (`*.globalValueSet-meta.xml`).

A global value set is a reusable picklist value list shared across many picklist
fields. Each becomes a single `globalvalueset/<Name>` node, with Name taken from
the filename; the `references` edges from picklist fields are emitted by
`objects.py`. The set's `<customValue>` entries are values and are never read —
only the name leaves this extractor.
"""
from __future__ import annotations

from pathlib import Path

from ..core import node

_SUFFIX = ".globalValueSet-meta.xml"


class GlobalValueSetExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(_SUFFIX)

    def extract(self, path: Path):
        name = path.name[: -len(_SUFFIX)]
        if not name:
            return [], []
        # Name only — the body holds the values, which are never read.
        return [node(f"globalvalueset/{name}", "globalvalueset", name)], []


EXTRACTORS = [GlobalValueSetExtractor()]
