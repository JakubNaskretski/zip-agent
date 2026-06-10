"""Join a Confluence graph to a Salesforce graph — page -> SF entity it documents.

Because Confluence content is messy, the cross-source link is a deliberate,
auditable step rather than an automatic build edge. :func:`join` reads two
already-built graphs and returns ``documents`` edges from Confluence ``page`` nodes
to the Salesforce nodes they reference, each tagged with ``via`` (how it matched)
and ``confidence`` so the caller keeps only what it trusts. It MUTATES NEITHER
graph. :func:`merge` unions both graphs plus chosen cross-edges into one
``{nodes, edges, unresolved, errors}`` when a combined graph is wanted.

Matching is conservative by default:
  - ``scan_urls`` (high)   — Salesforce Lightning URLs in the page body name an
    entity directly (``/lightning/o/<Object>/``, ``/lightning/r/<Object>/...``,
    ``/lightning/n/<TabName>``, ``/lightning/setup/ObjectManager/<Object>/...``).
  - ``match_titles`` (medium) — page title exactly equals an SF node label/name.
  - ``match_labels`` (low, OFF) — a page label exactly equals an SF node label.
  - ``scan_body`` (medium, OFF) — distinctive ``*__c``-style API names in the body
    text. Off because a broad identifier scan needs the SF-identifier precision
    work in HARDENING-BACKLOG.local.md (system-namespace denylist, ``__c``
    conflation) to avoid false edges.
"""
from __future__ import annotations

import re

_CONF_RANK = {"high": 3, "medium": 2, "low": 1}
# Same-confidence tie-break between match kinds, so the winning `via` for a
# (page, target) pair never depends on scan order (deterministic output).
_VIA_RANK = {"url": 4, "title": 3, "body": 2, "label": 1}

# SF Lightning URL shapes that NAME an entity (record-id-only URLs can't, so they
# are ignored): /lightning/o/<Object>/... · /lightning/r/<Object>/<id>/view ·
# /lightning/n/<TabName> (a named custom tab) · the Setup Object Manager path.
_URL_ENTITY = re.compile(r"/lightning/[orn]/(\w+)", re.I)
_URL_SETUP_OBJECT = re.compile(r"/lightning/setup/ObjectManager/(\w+)", re.I)
# Distinctive custom API names in free text (contain the "__" namespace separator).
_API_NAME = re.compile(r"\b\w+__\w+\b")


def _name(nid):
    return nid.split("/", 1)[-1] if isinstance(nid, str) and "/" in nid else (nid or "")


def _label_keys(n, nid):
    """Match keys for an SF node: its label, its id name segment, and (for ``__c``
    API names) the suffix-stripped base."""
    keys = set()
    label = n.get("label")
    if label:
        keys.add(str(label))
    name = _name(nid)
    if name:
        keys.add(name)
        if name.endswith("__c"):
            keys.add(name[:-3])
    return {k for k in keys if k}


def _sf_index(sf_graph):
    """Case-insensitive ``label/name -> {node ids}`` index over the SF graph."""
    idx = {}
    for n in (sf_graph or {}).get("nodes", []) or []:
        if not isinstance(n, dict):
            continue
        nid = n.get("id")
        if not nid:
            continue
        for key in _label_keys(n, nid):
            idx.setdefault(key.lower(), set()).add(nid)
    return idx


def _page_labels(confluence_graph):
    """``page id -> [label names]`` from ``labeled`` edges + confluencelabel nodes."""
    nbyid = {
        n["id"]: n
        for n in (confluence_graph or {}).get("nodes", []) or []
        if isinstance(n, dict) and n.get("id")
    }
    out = {}
    for e in (confluence_graph or {}).get("edges", []) or []:
        if isinstance(e, dict) and e.get("type") == "labeled":
            name = (nbyid.get(e.get("dst"), {}) or {}).get("label") or _name(e.get("dst"))
            if name:
                out.setdefault(e.get("src"), []).append(name)
    return out


def join(confluence_graph, sf_graph, *, match_titles=True, match_labels=False,
         scan_urls=True, scan_body=False, min_len=4, node_type="page") -> list:
    """Return ``documents`` cross-edges (content node -> SF node), deduped to the
    highest-confidence ``via`` per (source, target). Mutates nothing. Each edge is
    ``{"src", "type": "documents", "dst", "via", "confidence"}``.

    The scan is generic over any content-bearing node (``label`` + optional
    ``text`` / ``urls`` / ``labeled`` edges): ``node_type`` selects which — the
    Jira join reuses this with ``node_type="jiraissue"``.
    """
    idx = _sf_index(sf_graph)
    page_labels = _page_labels(confluence_graph) if match_labels else {}
    best = {}  # (page_id, sf_id) -> (rank, via, confidence)

    def add(page_id, candidate, via, confidence):
        if not candidate or len(candidate) < min_len:
            return
        rank = (_CONF_RANK[confidence], _VIA_RANK[via])
        for sf_id in idx.get(candidate.lower(), ()):
            key = (page_id, sf_id)
            if key not in best or rank > best[key][0]:
                best[key] = (rank, via, confidence)

    for n in (confluence_graph or {}).get("nodes", []) or []:
        if not isinstance(n, dict) or n.get("type") != node_type:
            continue
        page_id = n.get("id")
        if not page_id:
            continue
        body = n.get("text") or ""
        if scan_urls:
            haystack = body + " " + " ".join(n.get("urls") or [])
            for pat in (_URL_ENTITY, _URL_SETUP_OBJECT):
                for m in pat.finditer(haystack):
                    add(page_id, m.group(1), "url", "high")
        if match_titles:
            add(page_id, n.get("label") or _name(page_id), "title", "medium")
        if match_labels:
            for lbl in page_labels.get(page_id, ()):
                add(page_id, lbl, "label", "low")
        if scan_body:
            for m in _API_NAME.finditer(body):
                add(page_id, m.group(0), "body", "medium")

    return [
        {"src": page_id, "type": "documents", "dst": sf_id, "via": via, "confidence": confidence}
        for (page_id, sf_id), (_rank, via, confidence) in sorted(best.items())
    ]


def merge(sf_graph, confluence_graph, cross_edges=None) -> dict:
    """Union two graphs plus ``cross_edges`` into one ``{nodes, edges, unresolved,
    errors}``. First node seen for an id wins; edges (incl. cross-edges, which
    carry extra ``via``/``confidence`` keys) are concatenated. Inputs unchanged.
    """
    nodes = {}
    edges = []
    unresolved = []
    errors = []
    for g in (sf_graph, confluence_graph):
        g = g or {}
        for n in g.get("nodes", []) or []:
            if isinstance(n, dict) and n.get("id"):
                nodes.setdefault(n["id"], n)
        edges.extend(e for e in (g.get("edges", []) or []) if isinstance(e, dict))
        unresolved.extend(g.get("unresolved", []) or [])
        errors.extend(g.get("errors", []) or [])
    edges.extend(cross_edges or [])
    return {"nodes": list(nodes.values()), "edges": edges,
            "unresolved": unresolved, "errors": errors}
