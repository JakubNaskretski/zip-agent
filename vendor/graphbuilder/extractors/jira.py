"""Jira issue extractor — ``*.issue.json`` dumps from ``jira.collect``.

Emits the intra-Jira graph for one issue: a ``jiraissue`` node (carrying the
description text + envelope attrs — priority/resolution/created plus the REST id
and browse URL as identity) plus its project, labels, releases (fixVersions),
sprints, components and assignee/reporter/mentioned users, wired by ``child-of``
/ ``links-to`` / ``labeled`` / ``fixed-in`` / ``in-sprint`` / ``component-of`` /
``assigned-to`` / ``authored-by`` / ``mentions``. Epic membership is a second
``child-of`` to the epic's ``jiraissue`` node (consistent with subtask->parent —
an issue may be child-of BOTH its project and its epic). This is the SEPARATE
Jira graph — it never emits Salesforce or Confluence edges; wiring issues to the
SF nodes / Confluence pages they reference is the deliberate
:mod:`graphbuilder.jira.join` step.

Node ids: ``jiraproject/<KEY>`` · ``jiraissue/<ISSUE-KEY>`` · ``jiralabel/<name>``
· ``jirauser/<key>`` · ``jiraversion/<name>`` · ``jirasprint/<name>`` ·
``jiracomponent/<name>``. Issue keys are Jira's own stable identifiers, so issue
links resolve by exact key — an uncollected target becomes an external stub,
exactly like a Salesforce cross-file reference (plain StubResolver; no custom
resolver needed).
"""
from __future__ import annotations

from pathlib import Path

from ..core import node, raw_edge
from ..jira.parse import parse_issue


class JiraExtractor:
    source = "jira"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(".issue.json")

    def extract(self, path: Path):
        p = parse_issue(path)
        key = p.key or path.stem.replace(".issue", "")
        iid = f"jiraissue/{key}"

        # --- issue node (structure + the deliberate description-text capture) ---
        attrs = {"source": "jira"}
        if p.project_key:
            attrs["project_key"] = p.project_key
        if p.issue_type:
            attrs["issue_type"] = p.issue_type
        if p.status:
            attrs["status"] = p.status
        if p.priority:
            attrs["priority"] = p.priority
        if p.resolution:
            attrs["resolution"] = p.resolution
        if p.created:
            attrs["created"] = p.created
        if p.updated:
            attrs["updated"] = p.updated
        if p.id:                                  # numeric REST id (identity)
            attrs["rest_id"] = p.id
        if p.url:                                 # human browse URL (identity)
            attrs["url"] = p.url
        if p.urls:
            attrs["urls"] = list(dict.fromkeys(p.urls))
        if p.text:
            attrs["text"] = p.text
        nodes = [node(iid, "jiraissue", p.summary or key, **attrs)]

        edges: list[dict] = []
        seen: set[tuple] = set()

        def add_edge(etype, to_kind, to_name):
            if not to_name:
                return
            dedup = (etype, to_kind, to_name)
            if dedup in seen:
                return
            seen.add(dedup)
            edges.append(raw_edge(iid, etype, to_kind, to_name))

        # project node + containment (subtasks hang off their parent issue instead)
        if p.project_key:
            nodes.append(node(f"jiraproject/{p.project_key}", "jiraproject",
                              p.project_name or p.project_key, source="jira"))
            if p.parent_key:
                add_edge("child-of", "jiraissue", p.parent_key)
            else:
                add_edge("child-of", "jiraproject", p.project_key)
        # epic membership is a SECOND child-of, to the epic's issue node —
        # deliberately alongside the project/parent edge above.
        if p.epic_key and p.epic_key != key:
            add_edge("child-of", "jiraissue", p.epic_key)

        # issue links — typed in Jira (blocks/duplicates/relates), all graphed as
        # links-to: the raw-edge shape carries no extra attrs, and the type rarely
        # changes what a knowledge agent does with the edge.
        for _ltype, other in p.links:
            if other != key:
                add_edge("links-to", "jiraissue", other)
        for sub in p.subtasks:
            if sub != key:
                add_edge("links-to", "jiraissue", sub)

        # labels, releases, sprints, components + users are shared nodes (first
        # emitter wins in the registry)
        for lbl in dict.fromkeys(p.labels):
            nodes.append(node(f"jiralabel/{lbl}", "jiralabel", lbl, source="jira"))
            add_edge("labeled", "jiralabel", lbl)

        for ver in dict.fromkeys(p.fix_versions):
            nodes.append(node(f"jiraversion/{ver}", "jiraversion", ver, source="jira"))
            add_edge("fixed-in", "jiraversion", ver)

        for spr in dict.fromkeys(p.sprints):
            nodes.append(node(f"jirasprint/{spr}", "jirasprint", spr, source="jira"))
            add_edge("in-sprint", "jirasprint", spr)

        for comp in dict.fromkeys(p.components):
            nodes.append(node(f"jiracomponent/{comp}", "jiracomponent", comp, source="jira"))
            add_edge("component-of", "jiracomponent", comp)

        for user, etype in ((p.assignee, "assigned-to"), (p.reporter, "authored-by")):
            if user:
                nodes.append(node(f"jirauser/{user}", "jirauser", user, source="jira"))
                add_edge(etype, "jirauser", user)

        for user in dict.fromkeys(p.mentions):
            nodes.append(node(f"jirauser/{user}", "jirauser", user, source="jira"))
            add_edge("mentions", "jirauser", user)

        return nodes, edges


EXTRACTORS = [JiraExtractor()]
