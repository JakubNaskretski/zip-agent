"""Jira parsers — turn a collected issue dump into a typed dataclass.

An *issue dump* is the raw JSON of a single Jira Data Center REST issue (one file
per issue, written by :mod:`graphbuilder.jira.collect` from the ``search``
endpoint; optionally with the issue's remote links merged under ``_remotelinks``).
This module reads that envelope and scans the description's **wiki markup** for
the references Jira encodes as text:

    [~jdoe] · [~accountid:abc]          -> user mention
    PROJ-123 (in links/description)     -> issue reference (via issuelinks, not prose)
    [title|https://...] · bare URLs     -> link targets (for the cross-source joins)

Confidentiality: like the Confluence source (and unlike the names-only Salesforce
parsers), this DELIBERATELY captures the issue summary + description text as
agent-facing knowledge — keep any dump or built Jira graph local (gitignored).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class JIssue:
    """One parsed Jira issue (envelope + description references)."""
    key: str = ""                                    # e.g. ACME-101 (stable identifier)
    id: str = ""                                     # numeric REST id
    project_key: str = ""
    project_name: str = ""
    summary: str = ""
    issue_type: str = ""                             # Bug / Task / Sub-task / ...
    status: str = ""
    parent_key: str = ""                             # subtask parent ("" if none)
    labels: list = field(default_factory=list)       # label names
    assignee: str = ""                               # user key/name ("" if unassigned)
    reporter: str = ""
    links: list = field(default_factory=list)        # [(link_type, other_issue_key), ...]
    subtasks: list = field(default_factory=list)     # child issue keys
    mentions: list = field(default_factory=list)     # user keys mentioned in the text
    urls: list = field(default_factory=list)         # URLs in description + remote links
    updated: str = ""                                # ISO timestamp (the incremental check)
    text: str = ""                                   # description (the content capture)


# --------------------------------------------------------------------------- #
# wiki-markup scanners — tolerant: a bad/odd ref is skipped, never raised
# --------------------------------------------------------------------------- #
_MENTION = re.compile(r"\[~(?:accountid:)?([^\]\s]+)\]")
# [link text|https://...] wiki links AND bare URLs; ']'/'|'/quote/'>' end a URL
_URL = re.compile(r"https?://[^\s\]|>\"']+")


def iter_mentions(text: str) -> list:
    """User keys of every ``[~user]`` / ``[~accountid:...]`` mention."""
    return [m.group(1) for m in _MENTION.finditer(text or "") if m.group(1)]


def iter_urls(text: str) -> list:
    """Every URL in the wiki markup (bare or inside ``[text|url]`` links)."""
    return [m.group(0).rstrip(").,;") for m in _URL.finditer(text or "")]


def _dig(d, *keys, default=""):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _user(node) -> str:
    """A user reference from an assignee/reporter/author envelope: Data Center
    ``name``/``key`` first, Cloud-ish ``accountId``/``displayName`` as fallbacks."""
    if not isinstance(node, dict):
        return ""
    return str(node.get("name") or node.get("key") or node.get("accountId")
               or node.get("displayName") or "")


def parse_issue(path) -> JIssue:
    """Parse a collected issue-dump JSON file into a :class:`JIssue`.

    Tolerant of the REST envelope's optional fields: anything missing degrades to
    a default. A genuinely unreadable / non-JSON file is left to raise so the
    build records it in ``errors`` (the core wraps ``extract``), matching the
    other sources.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return JIssue()
    f = data.get("fields") if isinstance(data.get("fields"), dict) else {}

    links = []
    for ln in (f.get("issuelinks") or []):
        if not isinstance(ln, dict):
            continue
        ltype = _dig(ln, "type", "name") or "relates"
        other = _dig(ln, "outwardIssue", "key") or _dig(ln, "inwardIssue", "key")
        if other:
            links.append((str(ltype), str(other)))

    subtasks = [str(s.get("key")) for s in (f.get("subtasks") or [])
                if isinstance(s, dict) and s.get("key")]

    description = f.get("description")
    text = description if isinstance(description, str) else ""

    urls = iter_urls(text)
    for rl in (data.get("_remotelinks") or []):
        u = _dig(rl, "object", "url") if isinstance(rl, dict) else ""
        if u:
            urls.append(str(u))

    labels = [str(x) for x in (f.get("labels") or []) if x]

    return JIssue(
        key=str(data.get("key") or ""),
        id=str(data.get("id") or ""),
        project_key=str(_dig(f, "project", "key") or ""),
        project_name=str(_dig(f, "project", "name") or ""),
        summary=str(f.get("summary") or ""),
        issue_type=str(_dig(f, "issuetype", "name") or ""),
        status=str(_dig(f, "status", "name") or ""),
        parent_key=str(_dig(f, "parent", "key") or ""),
        labels=labels,
        assignee=_user(f.get("assignee")),
        reporter=_user(f.get("reporter")),
        links=links,
        subtasks=subtasks,
        mentions=iter_mentions(text),
        urls=list(dict.fromkeys(urls)),
        updated=str(f.get("updated") or ""),
        text=text,
    )
