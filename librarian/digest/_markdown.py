"""Markdown extractor — `.md` / `.markdown`, for the `docs` digest.

A local extractor (not in the vendored engine) that conforms to the engine's
Extractor protocol, so `office.parse_office` runs it alongside docx/xlsx/pptx and
a `.md` dropped in a docs upload becomes a document like any other: a `docfile`
node + a `docsection` tree from its ATX headings (`#`..`######`), wired by
`contains` / `child-of` exactly like the docx extractor. The same parser shape
serves the agent's work `.md` files conceptually, but those are authored through
:mod:`runtime.work` (one note node), not this digest path.

Fenced code blocks (``` / ~~~) are skipped so a `#` inside code isn't mistaken
for a heading. Markdown headings are explicit, so `structure` is always
``declared``; a heading-less file stays one flat `docfile` carrying its text
(searchable via the sidecar). Names/text only — no author metadata, no edges to
other sources (the docs prose rule).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


class MarkdownExtractor:
    source = "docs"

    def handles(self, path: Path) -> bool:
        return path.suffix.lower() in (".md", ".markdown")

    def extract(self, path: Path):
        from graphbuilder.core import node, raw_edge   # engine helpers (lazy)

        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        file_id = hashlib.sha1(raw).hexdigest()[:12]
        did = f"docfile/{file_id}"
        lines = text.splitlines()

        # headings (ATX), skipping fenced code blocks
        marks = []                                   # (line_index, level, title)
        in_fence = False
        for i, ln in enumerate(lines):
            if _FENCE.match(ln):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            m = _HEADING.match(ln)
            if m:
                marks.append((i, len(m.group(1)), m.group(2).strip()))

        first = marks[0][0] if marks else len(lines)
        preamble = "\n".join(lines[:first]).strip()
        title = next((t for (_, lvl, t) in marks if lvl == 1), None) or path.stem
        structure = "declared" if marks else "none"

        attrs = {"source": "docs", "doc_type": "markdown", "structure": structure}
        if title:
            attrs["title"] = title
        if preamble:
            attrs["text"] = preamble
        nodes = [node(did, "docfile", path.name, **attrs)]

        edges: list = []
        stack: list = []                             # [(level, ordinal)]
        for idx, (li, lvl, ttl) in enumerate(marks):
            ordinal = idx + 1
            nxt = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines)
            body = "\n".join(lines[li + 1:nxt]).strip()
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            parent = stack[-1][1] if stack else 0
            sname = f"{file_id}#{ordinal}"
            sattrs = {"source": "docs", "level": lvl}
            if body:
                sattrs["text"] = body
            nodes.append(node(f"docsection/{sname}", "docsection", ttl, **sattrs))
            if parent:
                edges.append(raw_edge(f"docsection/{sname}", "child-of",
                                      "docsection", f"{file_id}#{parent}"))
            else:
                edges.append(raw_edge(did, "contains", "docsection", sname))
            stack.append((lvl, ordinal))
        return nodes, edges


EXTRACTORS = [MarkdownExtractor()]
