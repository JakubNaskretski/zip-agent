"""Confluence source for graph-builder — a SEPARATE graph, joinable to Salesforce.

Layering mirrors the Salesforce side (parser <- extractor <- core):

  - :mod:`graphbuilder.confluence.collect`  — pull an on-prem Confluence Data
    Center space into a local dump (one raw REST ``content`` JSON per page) over
    the REST API with a Personal Access Token. Network I/O lives here only.
  - :mod:`graphbuilder.confluence.parse`    — parse a page dump (envelope +
    storage-format body) into a typed :class:`~graphbuilder.confluence.parse.CPage`.
  - :mod:`graphbuilder.extractors.confluence` — the auto-discovered extractor
    (``source = "confluence"``) that turns dumps into graph nodes/edges. Build it
    with the ordinary :func:`graphbuilder.build_graph` pointed at the dump dir;
    only this extractor's ``handles`` matches, so you get a Confluence-only graph.
  - :mod:`graphbuilder.confluence.join`     — :func:`join` / :func:`merge`: wire
    Confluence pages to the Salesforce nodes they document, on demand and
    auditably (the cross-source step is deliberate, never automatic).

Confidentiality: this source DELIBERATELY captures page body text (agent-facing
knowledge), unlike the names-only Salesforce extractors. Any dump or built
Confluence/joined graph therefore holds real content — keep it local (gitignored),
never commit or egress it.
"""
from __future__ import annotations

from .classify import apply_classifications
from .collect import collect
from .join import join, merge

__all__ = ["collect", "join", "merge", "apply_classifications"]
