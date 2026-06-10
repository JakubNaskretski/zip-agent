"""Jira source for graph-builder — a THIRD, separate graph, joinable to both
Salesforce and Confluence.

Layering mirrors the Confluence source exactly (collect -> parse -> extractor ->
join); see that package's docstring for the architecture. In short:

  - :mod:`graphbuilder.jira.collect`     — pull Jira Data Center project(s) into a
    local dump (one raw REST issue JSON per issue) over the REST API with a
    Personal Access Token (``$JIRA_TOKEN``, Bearer — same auth model as
    Confluence). Network I/O lives here only. Incremental + pruning like the
    Confluence collector.
  - :mod:`graphbuilder.jira.parse`       — parse an issue dump into a typed
    :class:`~graphbuilder.jira.parse.JIssue`.
  - :mod:`graphbuilder.extractors.jira`  — the auto-discovered extractor
    (``source = "jira"``) for ``*.issue.json``. Build with the ordinary
    :func:`graphbuilder.build_graph` pointed at the dump dir.
  - :mod:`graphbuilder.jira.join`        — :func:`join` (issue -> SF, reusing the
    generic content matcher) and :func:`join_confluence` (issue <-> page via
    page URLs and jira macros). Cross-source edges are a deliberate step, never
    a build edge.

Confidentiality: issue summaries/descriptions are captured as agent-facing
knowledge — dumps and built Jira graphs are sensitive-by-default; keep them
local (gitignored), never commit or egress them.

Scope: Jira Data Center / Server with PAT Bearer auth (8.14+). Jira Cloud
(different auth + API shapes) is out of scope, matching the Confluence source.
Boards/sprints (the agile API) are not collected — issues, projects, links,
labels and users are.
"""
from __future__ import annotations

from .collect import collect
from .join import join, join_confluence

__all__ = ["collect", "join", "join_confluence"]
