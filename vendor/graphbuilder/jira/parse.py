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
from functools import lru_cache
from pathlib import Path

# Customfield id -> display-name map written by the collector next to the dump
# (one ``/rest/api/2/field`` discovery per collect run), so Epic Link + Sprint —
# which live in instance-specific customfields on Data Center — resolve offline.
FIELDS_FILE = "_fields.json"


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
    priority: str = ""                               # priority name ("" if unset)
    resolution: str = ""                             # resolution name ("" if unresolved)
    parent_key: str = ""                             # subtask parent ("" if none)
    epic_key: str = ""                               # Epic Link issue key ("" if none)
    labels: list = field(default_factory=list)       # label names
    components: list = field(default_factory=list)   # project component names
    fix_versions: list = field(default_factory=list)  # release names (fixVersions)
    sprints: list = field(default_factory=list)      # sprint names
    assignee: str = ""                               # user key/name ("" if unassigned)
    reporter: str = ""
    links: list = field(default_factory=list)        # [(link_type, other_issue_key), ...]
    subtasks: list = field(default_factory=list)     # child issue keys
    mentions: list = field(default_factory=list)     # user keys mentioned in the text
    urls: list = field(default_factory=list)         # URLs in description + remote links
    created: str = ""                                # ISO timestamp
    updated: str = ""                                # ISO timestamp (the incremental check)
    url: str = ""                                    # browse URL derived from REST `self`
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


def _names(items) -> list:
    """``name`` of every dict in a REST list field (components, fixVersions)."""
    return [str(x.get("name")) for x in (items or [])
            if isinstance(x, dict) and x.get("name")]


def _browse_url(self_url, key) -> str:
    """Human browse URL from the REST ``self`` URL: everything before ``/rest/``
    (which keeps scheme+host+port AND any context path Jira lives under) +
    ``/browse/<KEY>``. Query strings never survive. "" when underivable."""
    base = str(self_url or "").split("?", 1)[0]
    if not key or "/rest/" not in base:
        return ""
    return f"{base.split('/rest/', 1)[0]}/browse/{key}"


# Data Center sprint customfield values are opaque toString() blobs like
# ``com.atlassian.greenhopper.service.sprint.Sprint@1f[id=5,...,name=Sprint 7,...]``
# — newer versions return dicts instead. ``[``/``,`` anchors the *name=* token so
# e.g. ``rapidViewName=`` can never match.
_SPRINT_NAME = re.compile(r"[\[,]name=([^,\]]*)")


def _sprint_names(value) -> list:
    """Sprint names from a sprint customfield value — a list (or single value) of
    greenhopper strings and/or dicts; both shapes tolerated, junk skipped."""
    names = []
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, dict):
            if item.get("name"):
                names.append(str(item["name"]))
        elif isinstance(item, str):
            m = _SPRINT_NAME.search(item)
            if m and m.group(1):
                names.append(m.group(1))
    return names


@lru_cache(maxsize=None)
def _load_fields_file(fields_path: str) -> dict:
    """The customfield id -> display-name map from one ``_fields.json`` (cached
    per path — a dump dir's map is read once, not per issue). {} on any problem."""
    try:
        data = json.loads(Path(fields_path).read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def _fields_map_for(issue_path: Path) -> dict:
    """Discover the dump's ``_fields.json`` for one issue file. The collector
    writes it at the dump root (issue files sit at ``<root>/<PROJECT>/``), so the
    grandparent is the normal hit; the parent covers flat layouts. Missing file
    -> {} (epic/sprint simply stay empty)."""
    for d in (issue_path.parent, issue_path.parent.parent):
        candidate = d / FIELDS_FILE
        if candidate.is_file():
            return _load_fields_file(str(candidate))
    return {}


def parse_issue(path, fields_map=None) -> JIssue:
    """Parse a collected issue-dump JSON file into a :class:`JIssue`.

    Tolerant of the REST envelope's optional fields: anything missing degrades to
    a default. A genuinely unreadable / non-JSON file is left to raise so the
    build records it in ``errors`` (the core wraps ``extract``), matching the
    other sources.

    ``fields_map`` (customfield id -> display name) resolves the Data Center
    Epic Link + Sprint customfields; by default it is discovered from the dump's
    ``_fields.json`` (written by the collector), and its absence just leaves
    ``epic_key`` / ``sprints`` empty.
    """
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return JIssue()
    f = data.get("fields") if isinstance(data.get("fields"), dict) else {}

    # Epic Link + Sprint sit in instance-specific customfields — resolve their
    # ids by display name (case-insensitive); tolerate either/both missing.
    if fields_map is None:
        fields_map = _fields_map_for(path)
    epic_key = ""
    sprints: list = []
    for fid, fname in fields_map.items():
        value = f.get(fid)
        if value is None:
            continue
        display = str(fname).strip().lower()
        if display == "epic link" and isinstance(value, str):
            epic_key = value
        elif display == "sprint":
            sprints = _sprint_names(value)

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

    key = str(data.get("key") or "")
    return JIssue(
        key=key,
        id=str(data.get("id") or ""),
        project_key=str(_dig(f, "project", "key") or ""),
        project_name=str(_dig(f, "project", "name") or ""),
        summary=str(f.get("summary") or ""),
        issue_type=str(_dig(f, "issuetype", "name") or ""),
        status=str(_dig(f, "status", "name") or ""),
        priority=str(_dig(f, "priority", "name") or ""),
        resolution=str(_dig(f, "resolution", "name") or ""),
        parent_key=str(_dig(f, "parent", "key") or ""),
        epic_key=epic_key,
        labels=labels,
        components=_names(f.get("components")),
        fix_versions=_names(f.get("fixVersions")),
        sprints=sprints,
        assignee=_user(f.get("assignee")),
        reporter=_user(f.get("reporter")),
        links=links,
        subtasks=subtasks,
        mentions=iter_mentions(text),
        urls=list(dict.fromkeys(urls)),
        created=str(f.get("created") or ""),
        updated=str(f.get("updated") or ""),
        url=_browse_url(data.get("self"), key),
        text=text,
    )
