"""Word document extractor — ``*.docx`` (OOXML zip; the legacy binary ``.doc``
is NOT OOXML and is rejected by ``handles``).

Emits the intra-document graph for one file: a ``docfile`` node (content-hash
id ``docfile/<sha1-12>``, filename as label, ``structure`` tier attr) plus its
``docsection`` tree (ordinal-stable ids ``docsection/<sha1-12>#<n>``, heading
text as label, section body text as the deliberate content capture — like
Confluence page bodies), wired by ``contains`` (docfile -> top-level section)
and ``child-of`` (section -> parent section). Structure detection is tiered
(declared / heuristic / none — see :mod:`graphbuilder.office`): heuristic
sections carry ``confidence: "heuristic"``; a structureless document honestly
stays a single flat ``docfile`` carrying the body text — sections are never
fabricated.

Names/labels/headers + section text only — author names are never read (the
docProps creator fields are deliberately skipped). A Word table contributes its
first-row cells as a ``columns`` attr on the owning section (or the docfile) —
never per-column nodes, never data rows. References detected in the text (Jira
keys / ``X__c`` API names / URLs) are ATTRS on the docfile, never edges —
wiring ``docs`` to other sources would be a deliberate later join, mirroring
Confluence ``jira_keys``.

A corrupt zip or malformed XML raises out of ``extract`` — the core records it
in ``errors``, so one bad file never kills a build.
"""
from __future__ import annotations

from pathlib import Path

from ..core import node, raw_edge
from ..office import parse_docx


class DocxExtractor:
    source = "docs"

    def handles(self, path: Path) -> bool:
        return path.suffix.lower() == ".docx"   # legacy binary .doc rejected

    def extract(self, path: Path):
        d = parse_docx(path)
        did = f"docfile/{d.file_id}"

        # --- docfile node (identity + tier + the flat/preamble content) ---
        attrs = {"source": "docs", "doc_type": "docx", "structure": d.structure}
        if d.title:
            attrs["title"] = d.title
        if d.modified:
            attrs["modified"] = d.modified
        if d.text:
            attrs["text"] = d.text
        if d.columns:
            attrs["columns"] = list(d.columns)
        if d.urls:
            attrs["urls"] = list(d.urls)
        if d.jira_keys:
            attrs["jira_keys"] = list(d.jira_keys)
        if d.sf_names:
            attrs["sf_names"] = list(d.sf_names)
        nodes = [node(did, "docfile", path.name, **attrs)]

        edges: list = []
        for s in d.sections:
            sname = f"{d.file_id}#{s.ordinal}"
            sattrs = {"source": "docs", "level": s.level}
            if s.confidence:                      # the trusted default ("declared")
                sattrs["confidence"] = s.confidence   # is not knowledge; deviation is
            if s.text:
                sattrs["text"] = s.text
            if s.columns:
                sattrs["columns"] = list(s.columns)
            nodes.append(node(f"docsection/{sname}", "docsection", s.title, **sattrs))
            if s.parent:
                edges.append(raw_edge(f"docsection/{sname}", "child-of",
                                      "docsection", f"{d.file_id}#{s.parent}"))
            else:
                edges.append(raw_edge(did, "contains", "docsection", sname))
        return nodes, edges


EXTRACTORS = [DocxExtractor()]
