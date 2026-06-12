"""Excel workbook extractor — ``*.xlsx`` plus macro-enabled ``*.xlsm`` (the
file gets ``has_macros: true``; macro CONTENT is never read). The binary
``.xlsb`` / legacy ``.xls`` are not OOXML zip+XML and are rejected by
``handles``.

Emits the intra-workbook graph for one file: a ``docfile`` node (content-hash
id ``docfile/<sha1-12>``, filename as label, ``doc_type: "xlsx"`` for the whole
format family, ``structure`` tier attr) plus a ``sheet`` node per worksheet
(``sheet/<sha1-12>#<name>``) and a ``datatable`` node per declared Excel Table
(``datatable/<sha1-12>#<name>`` — T1, the header row is *declared*), wired by
``contains`` (docfile -> sheet, sheet -> datatable).

The Excel value-zoo is neutralized by policy, not parsing heroics: NAMES ONLY —
sheet / table / column / defined names. Cell values and formula bodies (which
can embed business logic and endpoints) never enter the graph; the raw file
keeps them. Column headers are a ``columns`` ATTR, never per-column nodes. On a
sheet with no declared table, a gated first-row-as-header heuristic may supply
``columns`` + ``confidence: "heuristic"`` (see :mod:`graphbuilder.office`);
otherwise the sheet honestly carries dimensions only. References detected in
the captured NAMES (Jira keys / ``X__c`` API names / URLs from hyperlink rels)
are ATTRS on the docfile, never edges.

A corrupt zip or malformed XML raises out of ``extract`` — the core records it
in ``errors``, so one bad file never kills a build.
"""
from __future__ import annotations

from pathlib import Path

from ..core import node, raw_edge
from ..office import parse_xlsx, slug


class XlsxExtractor:
    source = "docs"

    def handles(self, path: Path) -> bool:
        # binary .xlsb / legacy .xls rejected (not OOXML)
        return path.suffix.lower() in (".xlsx", ".xlsm")

    def extract(self, path: Path):
        d = parse_xlsx(path)
        did = f"docfile/{d.file_id}"

        # --- docfile node (identity + tier + workbook-level names) ---
        attrs = {"source": "docs", "doc_type": "xlsx", "structure": d.structure}
        if d.has_macros:
            attrs["has_macros"] = True
        if d.sheets:
            attrs["sheet_count"] = len(d.sheets)
        if d.title:
            attrs["title"] = d.title
        if d.modified:
            attrs["modified"] = d.modified
        if d.defined_names:
            attrs["defined_names"] = list(d.defined_names)
        if d.urls:
            attrs["urls"] = list(d.urls)
        if d.jira_keys:
            attrs["jira_keys"] = list(d.jira_keys)
        if d.sf_names:
            attrs["sf_names"] = list(d.sf_names)
        nodes = [node(did, "docfile", path.name, **attrs)]

        edges: list = []
        for s in d.sheets:
            sname = f"{d.file_id}#{slug(s.name)}"
            sattrs = {"source": "docs"}
            if s.row_count:
                sattrs["row_count"] = s.row_count
            if s.col_count:
                sattrs["col_count"] = s.col_count
            if s.columns:                          # gated T2 header -> marked as a guess
                sattrs["columns"] = list(s.columns)
                sattrs["confidence"] = "heuristic"
            nodes.append(node(f"sheet/{sname}", "sheet", s.name, **sattrs))
            edges.append(raw_edge(did, "contains", "sheet", sname))

            for t in s.tables:                     # T1 — declared, no confidence attr
                tname = f"{d.file_id}#{slug(t.name)}"
                tattrs = {"source": "docs"}
                if t.columns:
                    tattrs["columns"] = list(t.columns)
                nodes.append(node(f"datatable/{tname}", "datatable", t.name, **tattrs))
                edges.append(raw_edge(f"sheet/{sname}", "contains", "datatable", tname))
        return nodes, edges


EXTRACTORS = [XlsxExtractor()]
