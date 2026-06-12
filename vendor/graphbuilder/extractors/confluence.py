"""Confluence page extractor — ``*.page.json`` dumps from ``confluence.collect``.

Emits the intra-Confluence graph for one page: a ``page`` node (carrying body text
+ metadata attrs) plus its space, attachments, labels and mentioned/authoring
users, wired by ``child-of`` / ``links-to`` / ``attaches`` / ``labeled`` /
``mentions`` / ``authored-by``. This is the SEPARATE Confluence graph — it never
emits Salesforce edges; wiring pages to the SF nodes they document is the
deliberate :func:`graphbuilder.confluence.join` step.

Node ids: ``space/<KEY>`` · ``page/<pageId>`` · ``attachment/<pageId>/<filename>``
· ``confluencelabel/<name>`` · ``confluenceuser/<key>``. Pages are **id-keyed**
(the REST page id is stable across renames and unique across spaces; the title is
the node ``label``), but storage-format links can only name their target as
(space, title) (``<ri:page ri:content-title ri:space-key>``), so ``links-to`` /
``child-of`` raw edges carry that title form and
:class:`graphbuilder.resolvers.PageResolver` maps it back to the id-keyed node —
or to a title-keyed external stub when the target page was never collected,
exactly like a Salesforce cross-file reference.
"""
from __future__ import annotations

from pathlib import Path

from ..confluence.parse import page_ref, parse_page, slug
from ..core import node, raw_edge


class ConfluenceExtractor:
    source = "confluence"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(".page.json")

    def extract(self, path: Path):
        p = parse_page(path)
        space = slug(p.space_key) or "_"
        title = p.title or p.id or path.stem
        page_name = page_ref(p.space_key or space, title)
        # id-keyed identity (stable across renames); title form only as a fallback
        # for a degenerate dump that carries no id.
        id_key = slug(p.id) or page_name
        pid = f"page/{id_key}"

        # --- page node (structure + the deliberate body-text content capture) ---
        attrs = {"source": "confluence"}
        if p.id:
            attrs["page_id"] = p.id
        if p.content_type and p.content_type != "page":
            attrs["content_type"] = p.content_type   # e.g. a blog post (same node type)
        if p.space_key:
            attrs["space_key"] = p.space_key
        if p.version:
            attrs["version"] = p.version
        if p.status and p.status != "current":
            # "current" is every live page's default — only deviations
            # (trashed / draft / archived) are knowledge
            attrs["status"] = p.status
        if p.created:
            attrs["created"] = p.created     # history.createdDate (when expanded)
        if p.updated:
            attrs["updated"] = p.updated     # version.when = last modified
        if p.ancestors:
            # full hierarchy chain root-first, as page ids (same string type as
            # page_id); parent_title above still drives the child-of edge
            attrs["ancestors"] = [a_id for a_id, _ in p.ancestors]
        if p.url:
            attrs["url"] = p.url
        if p.urls:
            attrs["urls"] = list(dict.fromkeys(p.urls))
        if p.tiny_links:
            # /x/<tinyId> short links: tiny ids are base64-ish encodings, NOT page
            # ids, so they cannot be resolved to a page node offline. Surfaced as
            # an attr only — never a links-to edge (a wrong edge is worse than
            # none); the agent at least sees an unresolved short-link exists.
            attrs["tiny_links"] = list(dict.fromkeys(p.tiny_links))
        if p.jira_keys:
            # attr only — wiring page -> jiraissue is the deliberate jira.join step
            attrs["jira_keys"] = list(dict.fromkeys(p.jira_keys))
        if p.body_text:
            attrs["text"] = p.body_text
        nodes = [node(pid, "page", title, **attrs)]

        # space node + child-of (parent page if any, else the space)
        sid = f"space/{slug(p.space_key or space)}"
        nodes.append(node(sid, "space", p.space_key or space, source="confluence"))

        edges: list[dict] = []
        seen: set[tuple] = set()

        def add_edge(etype, to_kind, to_name):
            if not to_name:
                return
            key = (etype, to_kind, to_name)
            if key in seen:
                return
            seen.add(key)
            edges.append(raw_edge(pid, etype, to_kind, to_name))

        if p.parent_title:
            add_edge("child-of", "page", page_ref(p.space_key or space, p.parent_title))
        else:
            add_edge("child-of", "space", slug(p.space_key or space))

        # page -> page links (default to this page's space when the link omits one);
        # skip a self-link.
        for link_title, link_space in p.links:
            target = page_ref(link_space or p.space_key or space, link_title)
            if target != page_name:
                add_edge("links-to", "page", target)

        # include / excerpt-include macros EMBED the target page's content here —
        # a transitive content dependency, stronger than (and emitted alongside)
        # the links-to the same <ri:page> also produces.
        for inc_title, inc_space in p.includes:
            target = page_ref(inc_space or p.space_key or space, inc_title)
            if target != page_name:
                add_edge("embeds", "page", target)

        # attachments — emit the node (real, with its filename label) + the edge;
        # keyed by the owning page's id so a page rename never re-keys them.
        for fn in dict.fromkeys(p.attachments):  # de-dup, preserve order
            a_name = f"{id_key}/{slug(fn)}"
            nodes.append(node(f"attachment/{a_name}", "attachment", fn, source="confluence"))
            add_edge("attaches", "attachment", a_name)

        # labels + users are shared nodes (first emitter wins in the registry), so
        # "pages with label X" / "pages mentioning user Y" fall out across the graph.
        for lbl in dict.fromkeys(p.labels):
            nodes.append(node(f"confluencelabel/{lbl}", "confluencelabel", lbl, source="confluence"))
            add_edge("labeled", "confluencelabel", lbl)

        for user in dict.fromkeys(p.mentions):
            nodes.append(node(f"confluenceuser/{user}", "confluenceuser", user, source="confluence"))
            add_edge("mentions", "confluenceuser", user)

        if p.author:
            nodes.append(node(f"confluenceuser/{p.author}", "confluenceuser", p.author, source="confluence"))
            add_edge("authored-by", "confluenceuser", p.author)

        return nodes, edges


EXTRACTORS = [ConfluenceExtractor()]
