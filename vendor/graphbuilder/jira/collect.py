"""Jira Data Center collector — pull project(s) into a local issue dump.

Network I/O for the Jira source lives ONLY here — and is STRICTLY read-only
(every request goes through :func:`graphbuilder.collectutil.get_json`, GET with
no body). It walks the REST ``search`` endpoint per project (JQL ``project = KEY
ORDER BY id ASC``) and writes each issue's raw API JSON to
``<out_dir>/<PROJECTKEY>/<KEY>.issue.json`` — exactly the shape
:mod:`graphbuilder.jira.parse` and the extractor expect. Collection is
incremental (an issue whose dump already holds the same ``updated`` timestamp is
not rewritten; vanished keys are pruned after a complete listing). Dependency-free
(stdlib ``urllib``); the HTTP ``opener`` is injectable so the collector is
testable with no network.

One extra GET to ``/rest/api/2/field`` at the start of each run discovers the
instance-specific customfield ids whose display names are "Epic Link" and
"Sprint" (Data Center keeps both in customfields). The discovered ids are
appended to the per-issue ``fields`` request and the id -> name map is written to
``<out_dir>/_fields.json`` so the parser resolves them offline; instances without
those fields are tolerated (the map is just empty).

Auth is a Personal Access Token sent as ``Authorization: Bearer <token>`` (Jira
8.14+ Data Center/Server — same model as the Confluence collector). The token is
read from the ``JIRA_TOKEN`` environment variable by default and is NEVER accepted
as a CLI flag, logged, or written into the dump.

``remote_links=True`` additionally fetches each issue's remote links (one extra
request PER ISSUE — the strongest issue->Confluence-page signal, but N+1; off by
default). They are merged into the dump under ``_remotelinks``.

Robustness mirrors the build: a per-project fetch failure stops *that* project and
is reported (never raised); a single un-writable issue is skipped and reported.
Only caller-fixable setup problems (missing token / base URL) raise.

Confidentiality: the dump holds real issue summaries + descriptions. Write it only
to a gitignored location (e.g. ``jira-dump/``); never commit or egress it.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..collectutil import build_opener, get_json, mark_incomplete, prune_dir, safe_segment
from .parse import FIELDS_FILE

# Everything the extractor needs, nothing it doesn't (no comments, no changelog,
# no worklogs, no attachments). The Epic Link / Sprint customfield ids discovered
# at run start are appended per request — this constant stays fixed.
_FIELDS = ("summary,description,issuetype,status,priority,resolution,components,"
           "fixVersions,created,labels,assignee,reporter,issuelinks,subtasks,"
           "parent,project,updated")
# Display names of the Data Center customfields worth collecting (their ids vary
# per instance — discovered via /rest/api/2/field; written to FIELDS_FILE).
_CUSTOMFIELD_NAMES = {"epic link", "sprint"}
_TOKEN_ENV = "JIRA_TOKEN"
_PER_PAGE = 50           # REST ``maxResults`` per request
_MAX_REQUESTS = 10_000   # hard cap on pagination requests per project (loop-safety)
# Project keys are interpolated into JQL — restrict to Jira's real key shape so a
# crafted "key" can't smuggle JQL.
_KEY_OK = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class CollectError(RuntimeError):
    """A caller-fixable setup problem (missing token / base URL / bad project
    key). Per-issue and per-project fetch failures are NOT raised — they are
    skipped and reported in the returned summary."""


def _updated_of(payload) -> str:
    """``fields.updated`` of an issue payload / existing dump content, "" when
    missing ("" never counts as unchanged, so it always rewrites)."""
    try:
        f = (payload or {}).get("fields") or {}
        return str(f.get("updated") or "")
    except AttributeError:
        return ""


def _existing_updated(path: Path) -> str:
    try:
        return _updated_of(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return ""


def _discover_customfields(opener, base, headers, timeout, sleep) -> dict:
    """One read-only GET to ``/rest/api/2/field``: the customfield id -> display
    name map for the names in ``_CUSTOMFIELD_NAMES`` (case-insensitive). Fields a
    given instance doesn't have are simply absent from the map."""
    payload = get_json(opener, f"{base}/rest/api/2/field", headers, timeout, sleep)
    mapping = {}
    for fld in payload if isinstance(payload, list) else []:
        if not isinstance(fld, dict):
            continue
        fid, name = str(fld.get("id") or ""), str(fld.get("name") or "")
        if fid and name.strip().lower() in _CUSTOMFIELD_NAMES:
            mapping[fid] = name
    return mapping


def _collect_project(opener, base, project, out_dir, headers, fields, per_page,
                     timeout, sleep, remote_links):
    """Page through one project, writing each issue dump. Returns a dict
    ``{written, unchanged, seen, complete, skipped, errors}`` — its own state, so
    projects can run on separate threads. Pagination stays sequential.

    Incremental: an issue whose dump already holds the same ``updated`` timestamp
    is left untouched (counted ``unchanged``) — and its remote links are not
    re-fetched. ``complete`` is False when pagination aborted on an error; then
    ``seen`` is partial and MUST NOT drive deletions.
    """
    written = unchanged = 0
    seen: set = set()
    complete = True
    skipped: list = []
    errors: list = []
    project_dir = out_dir / safe_segment(project)
    start = requests_made = 0
    while requests_made < _MAX_REQUESTS:
        requests_made += 1
        q = urllib.parse.urlencode({
            "jql": f"project = {project} ORDER BY id ASC",
            "startAt": start, "maxResults": per_page, "fields": fields,
        })
        try:
            payload = get_json(opener, f"{base}/rest/api/2/search?{q}", headers, timeout, sleep)
        except Exception as exc:  # can't page further in this project — report, move on
            errors.append({"project": project, "startAt": start,
                           "error": f"{type(exc).__name__}: {exc}"})
            complete = False
            break
        issues = payload.get("issues") or []
        if not issues:
            break
        for issue in issues:
            key = str((issue or {}).get("key") or "")
            if not key:
                skipped.append({"project": project, "reason": "issue with no key"})
                continue
            seen.add(key)
            target = project_dir / f"{safe_segment(key)}.issue.json"
            new_updated = _updated_of(issue)
            try:
                if new_updated and target.exists() \
                        and _existing_updated(target) == new_updated:
                    unchanged += 1
                    continue
                if remote_links:
                    try:
                        issue["_remotelinks"] = get_json(
                            opener, f"{base}/rest/api/2/issue/{urllib.parse.quote(key)}/remotelink",
                            headers, timeout, sleep)
                    except Exception as exc:  # links are an extra; the issue still lands
                        skipped.append({"project": project, "key": key,
                                        "error": f"remotelink: {type(exc).__name__}: {exc}"})
                project_dir.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
                written += 1
            except Exception as exc:
                skipped.append({"project": project, "key": key,
                                "error": f"{type(exc).__name__}: {exc}"})
        total = payload.get("total")
        start += len(issues)
        if isinstance(total, int) and start >= total:   # listed everything
            break
        if len(issues) < (payload.get("maxResults") or per_page):  # short page -> done
            break
    return {"written": written, "unchanged": unchanged, "seen": seen,
            "complete": complete, "skipped": skipped, "errors": errors}


def collect(base_url, project_keys, out_dir, *, token=None, per_page=_PER_PAGE,
            insecure=False, ca_bundle=None, opener=None, timeout=30, sleep=time.sleep,
            max_workers=None, remote_links=False, prune=True) -> dict:
    """Collect Jira issues for ``project_keys`` into ``out_dir``.

    ``base_url`` is the instance root (e.g. ``https://jira.example.internal``);
    ``project_keys`` a key or iterable of keys (validated — they are interpolated
    into JQL). ``token`` defaults to ``$JIRA_TOKEN`` (never pass a real token as a
    positional/CLI value). ``max_workers`` runs multiple projects concurrently
    (default ``min(8, n)``); pagination within a project stays sequential.
    ``ca_bundle`` (a PEM path) trusts a private CA with full verification —
    prefer it over ``insecure``.

    Incremental like the Confluence collector: unchanged ``updated`` timestamps
    are not rewritten; with ``prune`` (default), keys a COMPLETE listing no longer
    returns have their dump files deleted; an aborted project is reported in
    ``incomplete``, marked with a ``.incomplete`` sentinel, and never pruned.

    Each run starts with one GET to ``/rest/api/2/field`` to discover the Epic
    Link / Sprint customfield ids (instance-specific on Data Center); they are
    requested per issue and their id -> name map lands in ``<out_dir>/_fields.json``
    for the parser. A failed discovery degrades gracefully (reported in
    ``errors`` with ``project: None``; issues still collect, without those
    fields); the incremental/prune behaviour is untouched by it.

    Returns ``{"projects": {key: written}, "issues": N, "unchanged": N,
    "pruned": [keys...], "incomplete": [keys...], "skipped": [...], "errors": [...]}``
    — and never logs the token or issue content.
    """
    token = token if token is not None else os.environ.get(_TOKEN_ENV, "")
    if not token:
        raise CollectError(f"no Jira token: set ${_TOKEN_ENV} (never pass it as a flag)")
    if not base_url:
        raise CollectError("base_url is required, e.g. https://jira.example.internal")
    base = str(base_url).rstrip("/")
    if isinstance(project_keys, str):
        project_keys = [project_keys]
    projects = [p for p in (str(p).strip() for p in project_keys) if p]
    bad = [p for p in projects if not _KEY_OK.match(p)]
    if bad:
        raise CollectError(f"invalid project key(s): {', '.join(bad)}")
    opener = opener or build_opener(insecure, ca_bundle)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    out_dir = Path(out_dir)

    # Epic Link / Sprint customfield discovery — once per run, read-only, never
    # fatal: without it the run still collects everything except those two.
    customfields: dict = {}
    discovery_error = None
    if projects:
        try:
            customfields = _discover_customfields(opener, base, headers, timeout, sleep)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / FIELDS_FILE).write_text(
                json.dumps(customfields, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8")
        except Exception as exc:
            discovery_error = {"project": None,
                               "error": f"field discovery: {type(exc).__name__}: {exc}"}
    fields = ",".join([_FIELDS, *sorted(customfields)])

    def _run(project):
        return _collect_project(opener, base, project, out_dir, headers, fields,
                                per_page, timeout, sleep, remote_links)

    workers = max_workers if max_workers else min(8, max(1, len(projects)))
    if workers > 1 and len(projects) > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {p: ex.submit(_run, p) for p in projects}
            results = [(p, futures[p].result()) for p in projects]  # gather in order
    else:
        results = [(p, _run(p)) for p in projects]

    summary = {"projects": {}, "issues": 0, "unchanged": 0, "pruned": [],
               "incomplete": [], "skipped": [], "errors": []}
    for project, r in results:
        summary["projects"][project] = r["written"]
        summary["issues"] += r["written"]
        summary["unchanged"] += r["unchanged"]
        summary["skipped"].extend(r["skipped"])
        summary["errors"].extend(r["errors"])
        project_dir = out_dir / safe_segment(project)
        if r["complete"] and prune:
            summary["pruned"].extend(prune_dir(project_dir, r["seen"], ".issue.json"))
        if not r["complete"]:
            summary["incomplete"].append(project)
        mark_incomplete(project_dir, r["complete"])
    if discovery_error:
        summary["errors"].append(discovery_error)
    return summary
