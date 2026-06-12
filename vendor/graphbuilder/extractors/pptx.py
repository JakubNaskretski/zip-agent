"""PowerPoint presentation extractor — ``*.pptx`` plus macro-enabled ``*.pptm``
(the legacy binary ``.ppt`` is not OOXML zip+XML and is rejected by ``handles``).

Emits the intra-presentation graph for one file: a ``docfile`` node
(content-hash id ``docfile/<sha1-12>``, filename as label, ``doc_type: "pptx"``
for the whole format family, ``structure`` tier attr) plus the slide hierarchy
below it.

Structure detection (two tiers — PowerPoint does not support a "heuristic"
tier):

  T1 DECLARED   PowerPoint sections (``p14:sectionLst`` extension inside the
      presentation's ``extLst``) carry a human-readable name and an explicit
      membership list. Present → structure ``"declared"`` + one ``docsection``
      node per section; slides are wired ``docsection contains slide``.
  T3 NONE       No sections → structure ``"none"``; slides are still emitted
      (they are inherent presentation structure) and wired
      ``docfile contains slide``. Sections are never fabricated.

Per slide a ``slide`` node is emitted: id ``slide/<sha1-12>#<ordinal>``, label
= slide title (or ``"Slide <ordinal>"`` when there is no title), attrs:
``source``, ``ordinal``, and — only when non-empty — ``text`` (body runs),
``notes`` (speaker notes), ``columns`` (first-row of the first table, header
names only). Per chart found on a slide, a ``chart`` node is emitted: id
``chart/<sha1-12>#<n>``, label = chart title (or ``"Chart <n>"``), attrs
``series`` (series name strings) and ``categories`` (category label strings).
Numeric value caches are NEVER captured — names and labels only. A chart with
no readable text at all yields no node.

SmartArt body text and speaker notes are appended to the slide's ``text``
surface and land in the sidecar for FTS, not in separate nodes.

Author names, numeric data values and media content (images, audio, video) are
never read. References detected in the text surface (Jira keys / ``X__c`` API
names / URLs) are ATTRS on the ``docfile`` node, never edges — wiring ``docs``
to other sources is a deliberate later join, mirroring Confluence ``jira_keys``.

A corrupt zip or malformed XML raises out of ``extract`` — the core records it
in ``errors``, so one bad file never kills a build.
"""
from __future__ import annotations

from pathlib import Path

from ..core import node, raw_edge
from ..office import parse_pptx


class PptxExtractor:
    source = "docs"

    def handles(self, path: Path) -> bool:
        # legacy binary .ppt rejected (not OOXML)
        return path.suffix.lower() in (".pptx", ".pptm")

    def extract(self, path: Path):
        d = parse_pptx(path)
        did = f"docfile/{d.file_id}"

        # --- docfile node (identity + tier; no inline text on the docfile) ---
        attrs = {"source": "docs", "doc_type": "pptx", "structure": d.structure}
        if d.title:
            attrs["title"] = d.title
        if d.modified:
            attrs["modified"] = d.modified
        if d.slides:
            attrs["slide_count"] = len(d.slides)
        if d.urls:
            attrs["urls"] = list(d.urls)
        if d.jira_keys:
            attrs["jira_keys"] = list(d.jira_keys)
        if d.sf_names:
            attrs["sf_names"] = list(d.sf_names)
        nodes = [node(did, "docfile", path.name, **attrs)]

        edges: list = []

        # --- section nodes (T1 only — never fabricated) ---------------------
        section_for_slide: dict = {}   # slide_ordinal -> section_ordinal
        for sec in d.sections:
            sname = f"{d.file_id}#s{sec.ordinal}"
            sattrs = {"source": "docs"}
            # confidence omitted: declared is the trusted default
            nodes.append(node(f"docsection/{sname}", "docsection", sec.name, **sattrs))
            edges.append(raw_edge(did, "contains", "docsection", sname))
            for sl_ord in sec.slide_ordinals:
                section_for_slide[sl_ord] = sec.ordinal

        # --- slide nodes ----------------------------------------------------
        chart_seq = 0
        for sl in d.slides:
            sl_seg = f"{d.file_id}#{sl.ordinal}"
            sl_label = sl.title or f"Slide {sl.ordinal}"
            slattrs = {"source": "docs", "ordinal": sl.ordinal}
            if sl.text:
                slattrs["text"] = sl.text
            if sl.notes:
                slattrs["notes"] = sl.notes
            if sl.columns:
                slattrs["columns"] = list(sl.columns)
            nodes.append(node(f"slide/{sl_seg}", "slide", sl_label, **slattrs))

            # wire to parent (section or docfile)
            sec_ord = section_for_slide.get(sl.ordinal)
            if sec_ord is not None:
                parent_id = f"docsection/{d.file_id}#s{sec_ord}"
                edges.append(raw_edge(parent_id, "contains", "slide", sl_seg))
            else:
                edges.append(raw_edge(did, "contains", "slide", sl_seg))

            # --- chart nodes -----------------------------------------------
            for ch in sl.charts:
                chart_seq += 1
                ch_seg = f"{d.file_id}#{chart_seq}"
                ch_label = ch.title or f"Chart {chart_seq}"
                chattrs = {"source": "docs"}
                if ch.series:
                    chattrs["series"] = list(ch.series)
                if ch.categories:
                    chattrs["categories"] = list(ch.categories)
                nodes.append(node(f"chart/{ch_seg}", "chart", ch_label, **chattrs))
                edges.append(raw_edge(f"slide/{sl_seg}", "contains", "chart", ch_seg))

        return nodes, edges


EXTRACTORS = [PptxExtractor()]
