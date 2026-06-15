"""DataWeave extractor (``**/*.dwl``) — Phase-5 Mule taxonomy.

Per ``.dwl`` script:
  - a ``dataweave`` node keyed by its rel path, carrying its importable
    ``module`` name, ``dw_version`` and ``output`` mime (and ``inputs`` names);
  - an ``imports`` edge per ``import`` to a ``dwmodule`` target — resolved to the
    LOCAL ``.dwl`` that declares that module (its ``dataweave`` node), else an
    external ``dwmodule`` stub for a std-library / out-of-tree module
    (``DwModuleResolver``), exactly like a cross-file Salesforce reference;
  - a ``defines`` edge + ``dwfunction`` node per top-level ``fun``.

Names only — DataWeave body logic and values never enter the graph. Source is
``mule`` so the records join the Mule graph alongside flows/connectors.
"""
from __future__ import annotations

from pathlib import Path

from ..core import node, raw_edge
from ..dataweave import parse_dataweave
from ..mulesoft import is_dataweave_path


class DataWeaveExtractor:
    source = "mule"

    def handles(self, path: Path) -> bool:
        return is_dataweave_path(path)

    def extract(self, path: Path):
        s = parse_dataweave(path)
        did = f"dataweave/{s.file}"
        attrs = {"file": s.file}
        if s.module:
            attrs["module"] = s.module
        if s.version:
            attrs["dw_version"] = s.version
        if s.output:
            attrs["output"] = s.output
        if s.inputs:
            attrs["inputs"] = ",".join(s.inputs)        # directive NAMES only
        nodes = [node(did, "dataweave", s.file.rsplit("/", 1)[-1], **attrs)]
        edges = [raw_edge(did, "imports", "dwmodule", m) for m in s.imports]
        for fn in s.funcs:
            edges.append(raw_edge(did, "defines", "dwfunction", f"{s.file}#{fn}"))
            nodes.append(node(f"dwfunction/{s.file}#{fn}", "dwfunction", fn, file=s.file))
        return nodes, edges


EXTRACTORS = [DataWeaveExtractor()]
