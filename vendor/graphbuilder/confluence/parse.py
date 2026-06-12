"""Confluence parsers — turn a collected page dump into a typed dataclass.

A *page dump* is the raw JSON of a single Confluence Data Center REST ``content``
response (one file per page, written by :mod:`graphbuilder.confluence.collect`).
This module reads that envelope and scans the page's **storage-format** body
(XHTML) for the structural references Confluence encodes as markup:

    <ac:link><ri:page ri:content-title="T" ri:space-key="S"/></ac:link>   -> page link
    <ac:image|ac:link><ri:attachment ri:filename="F"/></...>             -> attachment
    <ri:user ri:userkey="K"/>                                            -> user mention

Why regex and not ``xml.etree``: the storage body is an XHTML *fragment* that
declares neither the ``ac:``/``ri:`` namespaces nor a single root element, so an
XML parser raises on the unbound prefixes. Scanning with regex + entity-unescape
is both robust to that and gives the contract's "skip the odd ref, never raise"
behaviour for malformed markup.

Confidentiality: unlike the Salesforce parsers (names/structure only), this
DELIBERATELY captures page body text (``body_text``) as agent-facing knowledge, so
the result is sensitive — keep any built Confluence graph local (gitignored).
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CPage:
    """One parsed Confluence content unit (envelope + storage-format references).
    Blog posts share the exact dump shape; ``content_type`` says which it was."""
    id: str = ""
    title: str = ""
    content_type: str = "page"                       # "page" | "blogpost"
    space_key: str = ""
    parent_id: str = ""                              # immediate parent page id ("" if top-level)
    parent_title: str = ""                           # immediate parent page title ("" if top-level)
    ancestors: list = field(default_factory=list)    # [(id, title), ...] root-first
    labels: list = field(default_factory=list)       # label names
    author: str = ""                                 # last-version author's key/name ("" if absent)
    version: int = 0
    status: str = ""                                 # REST status: "current"/"trashed"/"draft"/"archived"
    created: str = ""                                # history.createdDate ISO string ("" if not expanded)
    updated: str = ""                                # version.when ISO string ("" if absent)
    url: str = ""                                    # absolute web URL if derivable, else ""
    links: list = field(default_factory=list)        # [(title, space_key_or_empty), ...]
    includes: list = field(default_factory=list)     # include/excerpt-include macro targets, same shape
    jira_keys: list = field(default_factory=list)    # issue keys from jira macros
    attachments: list = field(default_factory=list)  # filenames
    mentions: list = field(default_factory=list)     # user keys
    urls: list = field(default_factory=list)         # external href / ri:url values (for the SF join)
    tiny_links: list = field(default_factory=list)   # /x/<tinyId> short-link ids (unresolvable offline)
    body_text: str = ""                              # plain-text body (the content capture)


# --------------------------------------------------------------------------- #
# storage-format scanners — tolerant: a bad/odd ref is skipped, never raised
# --------------------------------------------------------------------------- #
_RI_PAGE = re.compile(r"<ri:page\b([^>]*)>", re.I)
# Page-include / excerpt-include macros: their <ri:page> target is page CONTENT
# embedded here (a transitive dependency), not just a navigational link.
_INCLUDE_MACRO = re.compile(
    r'<ac:structured-macro\b[^>]*ac:name\s*=\s*"(?:include|excerpt-include)"[^>]*>'
    r"(.*?)</ac:structured-macro>",
    re.I | re.S,
)
# Jira macros embed an issue by key. The page node carries the keys as an attr
# only — wiring page -> jiraissue is the deliberate graphbuilder.jira.join step
# (cross-source edges never come from a build).
_JIRA_MACRO = re.compile(
    r'<ac:structured-macro\b[^>]*ac:name\s*=\s*"jira"[^>]*>(.*?)</ac:structured-macro>',
    re.I | re.S,
)
_JIRA_KEY_PARAM = re.compile(
    r'<ac:parameter\b[^>]*ac:name\s*=\s*"key"[^>]*>\s*([A-Za-z][A-Za-z0-9_]*-\d+)\s*<',
    re.I,
)
_RI_ATTACH = re.compile(r"<ri:attachment\b([^>]*)>", re.I)
_RI_USER = re.compile(r"<ri:user\b([^>]*)>", re.I)

_CONTENT_TITLE = re.compile(r'ri:content-title\s*=\s*"([^"]*)"', re.I)
_SPACE_KEY = re.compile(r'ri:space-key\s*=\s*"([^"]*)"', re.I)
_FILENAME = re.compile(r'ri:filename\s*=\s*"([^"]*)"', re.I)
_USERKEY = re.compile(r'ri:userkey\s*=\s*"([^"]*)"', re.I)
_USERNAME = re.compile(r'ri:username\s*=\s*"([^"]*)"', re.I)
_ACCOUNTID = re.compile(r'ri:account-id\s*=\s*"([^"]*)"', re.I)
_HREF = re.compile(r'href\s*=\s*"([^"]*)"', re.I)
_RI_URL = re.compile(r'<ri:url\b[^>]*?ri:value\s*=\s*"([^"]*)"', re.I)
# Confluence "tiny" short link: an href of the form /x/<tinyId> (relative) or
# https://host/x/<tinyId> (absolute). The tiny id is a URL-safe-base64-ish
# ENCODING of an internal page pointer, NOT a page id — it cannot be resolved to
# a page node offline. Matched against the WHOLE href (fullmatch) so ordinary
# paths that merely contain "/x/" don't false-positive; no "/" in the id class,
# so deeper paths under /x/ never match.
_TINY_HREF = re.compile(r"(?:https?://[^/]+)?/x/([A-Za-z0-9+_=-]+)/?", re.I)

_CDATA = re.compile(r"<!\[CDATA\[|\]\]>")
_CDATA_BLOCK = re.compile(r"<!\[CDATA\[.*?\]\]>", re.S)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def slug(s: str) -> str:
    """Collapse path separators so a space key / title / filename is safe inside a
    ``type/name`` id (the name segment must not introduce stray ``/`` hops).
    Applied identically when naming a node and when naming a reference target, so
    the two always match."""
    return (s or "").replace("/", "_").replace("\\", "_").strip()


def page_ref(space: str, title: str) -> str:
    """The ``<space>/<title>`` name segment a storage-format reference carries for
    a page. Page NODES are id-keyed (``page/<pageId>``); this title form is how
    links *name* their target, and the page resolver maps it back to the id."""
    return f"{slug(space)}/{slug(title)}"


def _attr(attrs: str, pat: re.Pattern) -> str:
    """First value of attribute ``pat`` in an element's attribute string, entity-
    unescaped and stripped; ``""`` if absent."""
    m = pat.search(attrs or "")
    return html.unescape(m.group(1)).strip() if m else ""


def iter_page_links(storage: str) -> list:
    """``[(content_title, space_key_or_empty), ...]`` for every ``<ri:page>`` in a
    storage body. A ``ri:page`` with no title is odd → skipped."""
    out = []
    for m in _RI_PAGE.finditer(storage or ""):
        title = _attr(m.group(1), _CONTENT_TITLE)
        if title:
            out.append((title, _attr(m.group(1), _SPACE_KEY)))
    return out


def iter_include_targets(storage: str) -> list:
    """``[(content_title, space_key_or_empty), ...]`` for every page targeted by an
    include / excerpt-include macro — the embedding (content) subset of
    :func:`iter_page_links`."""
    out = []
    for m in _INCLUDE_MACRO.finditer(storage or ""):
        out.extend(iter_page_links(m.group(1)))
    return out


def iter_jira_keys(storage: str) -> list:
    """Issue keys embedded by jira macros (``<ac:parameter ac:name="key">``)."""
    out = []
    for m in _JIRA_MACRO.finditer(storage or ""):
        for km in _JIRA_KEY_PARAM.finditer(m.group(1)):
            out.append(km.group(1).upper())
    return out


def iter_attachment_refs(storage: str) -> list:
    """Filenames of every ``<ri:attachment>`` in a storage body (skips ones with
    no filename)."""
    out = []
    for m in _RI_ATTACH.finditer(storage or ""):
        fn = _attr(m.group(1), _FILENAME)
        if fn:
            out.append(fn)
    return out


def iter_user_mentions(storage: str) -> list:
    """User keys of every ``<ri:user>`` mention (``ri:userkey`` on Data Center,
    falling back to ``ri:username`` on older DC, then ``ri:account-id``); skips
    ones with none of the three."""
    out = []
    for m in _RI_USER.finditer(storage or ""):
        key = (_attr(m.group(1), _USERKEY) or _attr(m.group(1), _USERNAME)
               or _attr(m.group(1), _ACCOUNTID))
        if key:
            out.append(key)
    return out


def iter_external_urls(storage: str) -> list:
    """External link targets in a storage body: ``<a href="...">`` values and
    ``<ri:url ri:value="..."/>`` macro links. The SF join scans these for Salesforce
    org URLs (which live in attributes, not body text). Entity-unescaped; skips empties."""
    out = []
    for pat in (_HREF, _RI_URL):
        for m in pat.finditer(storage or ""):
            u = html.unescape(m.group(1)).strip()
            if u:
                out.append(u)
    return out


def iter_tiny_links(storage: str) -> list:
    """Tiny ids of every ``/x/<tinyId>`` short-link href (relative or absolute) in
    a storage body. Tiny ids are base64-ish encodings — NOT page ids — so the
    extractor surfaces them as a page attr only, never a ``links-to`` edge (a
    wrong edge is worse than none). Non-tiny hrefs are skipped."""
    out = []
    for m in _HREF.finditer(storage or ""):
        tm = _TINY_HREF.fullmatch(html.unescape(m.group(1)).strip())
        if tm:
            out.append(tm.group(1))
    return out


def body_text(storage: str) -> str:
    """Best-effort plain text of a storage body — CDATA delimiters dropped (code
    kept), tags stripped, entities unescaped, whitespace collapsed. Tolerant:
    ``""`` on falsy input."""
    if not storage:
        return ""
    text = _CDATA.sub(" ", storage)
    text = _TAG.sub(" ", text)
    return _WS.sub(" ", html.unescape(text)).strip()


def _dig(d, *keys, default=""):
    """Nested ``dict.get`` walk: return ``d[k1][k2]...`` or ``default`` if any hop
    is missing / not a dict."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def parse_page(path) -> CPage:
    """Parse a collected page-dump JSON file into a :class:`CPage`.

    Tolerant of the REST envelope's optional/expanded fields: anything missing
    degrades to a default, and the storage scanners never raise on odd markup.
    A genuinely unreadable / non-JSON file is left to raise so the build records
    it in ``errors`` (the core wraps ``extract``), matching the Salesforce side.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return CPage()

    storage = _dig(data, "body", "storage", "value") or ""
    # Code/noformat macros wrap their content in CDATA; example markup or URLs in
    # there are NOT real references, so the reference scanners see the body with
    # CDATA blocks removed. body_text keeps the code (it IS page content).
    scan_src = _CDATA_BLOCK.sub(" ", storage)

    ancestors = [
        (str(a.get("id")), str(a.get("title") or ""))
        for a in (data.get("ancestors") or [])
        if isinstance(a, dict) and a.get("id") is not None
    ]
    labels = [
        (lbl.get("name") or lbl.get("label") or "")
        for lbl in (_dig(data, "metadata", "labels", "results", default=[]) or [])
        if isinstance(lbl, dict)
    ]
    labels = [x for x in labels if x]

    author = (
        _dig(data, "version", "by", "userKey")
        or _dig(data, "version", "by", "username")
        or _dig(data, "version", "by", "publicName")
        or _dig(data, "history", "createdBy", "userKey")
        or _dig(data, "history", "createdBy", "username")
        or ""
    )
    try:
        version = int(_dig(data, "version", "number", default=0) or 0)
    except (TypeError, ValueError):
        version = 0

    base = _dig(data, "_links", "base")
    webui = _dig(data, "_links", "webui")
    url = (base + webui) if (base and webui) else (webui or "")

    return CPage(
        id=str(data.get("id") or ""),
        title=str(data.get("title") or ""),
        content_type=str(data.get("type") or "page"),
        space_key=str(_dig(data, "space", "key") or ""),
        parent_id=ancestors[-1][0] if ancestors else "",
        parent_title=ancestors[-1][1] if ancestors else "",
        ancestors=ancestors,
        labels=labels,
        author=str(author or ""),
        version=version,
        status=str(data.get("status") or ""),
        # history is only present when the collector expanded it — tolerate absence
        created=str(_dig(data, "history", "createdDate") or ""),
        updated=str(_dig(data, "version", "when") or ""),
        url=str(url or ""),
        links=iter_page_links(scan_src),
        includes=iter_include_targets(scan_src),
        jira_keys=iter_jira_keys(scan_src),
        attachments=iter_attachment_refs(scan_src),
        mentions=iter_user_mentions(scan_src),
        urls=iter_external_urls(scan_src),
        tiny_links=iter_tiny_links(scan_src),
        body_text=body_text(storage),
    )
