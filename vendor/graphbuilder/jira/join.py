"""Join a Jira graph to the Salesforce and/or Confluence graphs.

Like the Confluence join, the cross-source link is a deliberate, auditable step —
never a build edge. Both functions read already-built graphs, mutate neither, and
tag every returned edge with ``via`` / ``confidence`` so the caller keeps only
what it trusts. ``graphbuilder.confluence.join.merge`` unions graphs + chosen
cross-edges (call it once per added source).

  - :func:`join` — issue -> SF node it documents. Reuses the (generic) Confluence
    matcher over ``jiraissue`` nodes: Lightning URLs in the description (high),
    exact summary match (medium, usually too fuzzy — off), opt-in body scan.
  - :func:`join_confluence` — issue <-> page, both directions:
      * issue -> page (``links-to``, high): a Confluence page URL in the issue
        (description or collected remote links) names the page by id or
        /display/SPACE/Title.
      * page -> issue (``links-to``, high): a jira macro on the page embeds the
        issue by key (collected as the page node's ``jira_keys`` attr).
    Edges are only returned when BOTH endpoints exist in the given graphs.
"""
from __future__ import annotations

import re
import urllib.parse

from ..confluence.join import join as _content_join

# Confluence Data Center page URL shapes that NAME a page:
#   .../pages/viewpage.action?pageId=12345   (id — exact)
#   .../display/SPACE/Page+Title             (space + title)
_PAGE_ID_URL = re.compile(r"[?&]pageId=(\d+)")
_DISPLAY_URL = re.compile(r"/display/([^/?#\s]+)/([^/?#\s]+)")


def join(jira_graph, sf_graph, *, match_titles=False, match_labels=False,
         scan_urls=True, scan_body=False, min_len=4) -> list:
    """Return ``documents`` cross-edges (jiraissue -> SF node). Same contract and
    knobs as the Confluence join; ``match_titles`` defaults OFF here because an
    issue summary is a sentence, not an entity name (exact hits would be flukes).
    """
    return _content_join(jira_graph, sf_graph, node_type="jiraissue",
                         match_titles=match_titles, match_labels=match_labels,
                         scan_urls=scan_urls, scan_body=scan_body, min_len=min_len)


def _nodes(graph, ntype):
    for n in (graph or {}).get("nodes", []) or []:
        if isinstance(n, dict) and n.get("type") == ntype and n.get("id"):
            yield n


def _page_indexes(confluence_graph):
    """Two lookups over collected (non-external) pages: by REST page id, and by
    (space key, title) — both how a URL can name a page."""
    by_id, by_title = {}, {}
    for n in _nodes(confluence_graph, "page"):
        if n.get("external"):
            continue
        if n.get("page_id"):
            by_id.setdefault(str(n["page_id"]), n["id"])
        space, title = n.get("space_key"), n.get("label")
        if space and title:
            by_title.setdefault((str(space).lower(), str(title).lower()), n["id"])
    return by_id, by_title


def join_confluence(jira_graph, confluence_graph) -> list:
    """Return ``links-to`` cross-edges between issues and pages (both directions,
    deduped, sorted). Mutates nothing."""
    by_id, by_title = _page_indexes(confluence_graph)
    issues = {n["id"]: n for n in _nodes(jira_graph, "jiraissue") if not n.get("external")}

    out = {}

    def add(src, dst, via):
        out.setdefault((src, dst), {"src": src, "type": "links-to", "dst": dst,
                                    "via": via, "confidence": "high"})

    # issue -> page: Confluence URLs in the issue's collected links/description
    for iid, n in issues.items():
        for url in n.get("urls") or []:
            m = _PAGE_ID_URL.search(url)
            if m and m.group(1) in by_id:
                add(iid, by_id[m.group(1)], "url")
                continue
            m = _DISPLAY_URL.search(url)
            if m:
                space = urllib.parse.unquote_plus(m.group(1)).lower()
                title = urllib.parse.unquote_plus(m.group(2)).lower()
                hit = by_title.get((space, title))
                if hit:
                    add(iid, hit, "url")

    # page -> issue: jira macros embed the issue by key
    for n in _nodes(confluence_graph, "page"):
        for key in n.get("jira_keys") or []:
            target = f"jiraissue/{key}"
            if target in issues:
                add(n["id"], target, "jira-macro")

    return [out[k] for k in sorted(out)]
