"""MUnit test extractor (``src/test/munit/**/*.xml``) — Phase-5 Mule taxonomy.

Per ``<munit:test>``:
  - a ``munittest`` node keyed ``munittest/<rel>#<name>`` (attrs: ``file``,
    optional ``ignore``; the test ``description`` is free prose — possibly
    secrets/PII — so it is deliberately not captured, per the names-only rule);
  - a ``tests`` edge to the ``muleflow`` each ``<flow-ref>`` exercises — the
    coverage signal, resolving to the real flow node or an external stub (so
    "which flows have tests" is a graph query);
  - a ``mocks`` edge to the ``muleconnector`` namespace each
    ``<munit-tools:mock-when processor="…">`` stubs.

MUnit files ARE Mule XML, so this reuses the same ElementTree parse as the
config extractor. Names only — assertion payloads / mocked values never enter
the graph. Source is ``mule``.
"""
from __future__ import annotations

from pathlib import Path

from ..core import node, raw_edge
from ..mulesoft import is_munit_path, parse_munit


class MunitExtractor:
    source = "mule"

    def handles(self, path: Path) -> bool:
        return is_munit_path(path)

    def extract(self, path: Path):
        nodes: list = []
        edges: list = []
        for t in parse_munit(path):
            tid = f"munittest/{t.file}#{t.name}"
            attrs = {"file": t.file}
            if t.ignore:
                attrs["ignore"] = True
            nodes.append(node(tid, "munittest", t.name, **attrs))
            for flow in t.flow_refs:
                edges.append(raw_edge(tid, "tests", "muleflow", flow))
            for conn in t.mocks:
                nodes.append(node(f"muleconnector/{conn}", "muleconnector", conn))
                edges.append(raw_edge(tid, "mocks", "muleconnector", conn))
        return nodes, edges


EXTRACTORS = [MunitExtractor()]
