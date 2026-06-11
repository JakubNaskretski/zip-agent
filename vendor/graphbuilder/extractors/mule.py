"""MuleSoft application config extractor (``src/main/mule/**/*.xml``).

Phase-1 / back-compat parity with the hand-rolled zip-agent digest. For each Mule
config file:
  - every ``<flow>`` / ``<sub-flow>`` becomes a ``muleflow`` node (attrs ``kind`` =
    flow|sub-flow, ``file`` = rel path);
  - a ``<flow-ref>`` becomes a ``calls`` edge (``muleflow`` -> ``muleflow``); an
    undefined target resolves to an ``external`` stub, exactly like a cross-file
    Salesforce reference;
  - a connector namespace a flow uses becomes a ``muleconnector`` node + a ``uses``
    edge.

This is the SEPARATE Mule graph (a fourth alongside Salesforce/Confluence/Jira):
it parses a static on-disk tree, so — like Salesforce — there is no collect step.
The two node types and two edge types here are frozen for back-compat with the
zip-agent runtime helpers (``who_calls`` / ``connectors_used`` / …); richer
artifacts (APIkit, properties, pom, MUnit, DataWeave) are added in later phases.
"""
from __future__ import annotations

from pathlib import Path

from ..core import node, raw_edge
from ..mulesoft import is_config_path, parse_config


class MuleConfigExtractor:
    source = "mule"

    def handles(self, path: Path) -> bool:
        return is_config_path(path)

    def extract(self, path: Path):
        nodes: list[dict] = []
        edges: list[dict] = []
        for f in parse_config(path):
            fid = f"muleflow/{f.name}"
            nodes.append(node(fid, "muleflow", f.name, kind=f.kind, file=f.file))
            for c in sorted(f.connectors):
                nodes.append(node(f"muleconnector/{c}", "muleconnector", c))
                edges.append(raw_edge(fid, "uses", "muleconnector", c))
            for ref in sorted(f.refs):
                edges.append(raw_edge(fid, "calls", "muleflow", ref))
        return nodes, edges


EXTRACTORS = [MuleConfigExtractor()]
