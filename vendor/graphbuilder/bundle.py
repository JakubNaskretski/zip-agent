"""Knowledge-base bundle — package built graphs + their source content as a
portable zip of text/JSON (no database).

Two layers joined by pointers:
  - ``content/`` : the FULL retrieved data as flat files — Confluence page bodies as
    ``.txt`` plus a raw storage ``.xhtml`` sidecar, Jira issue summaries +
    descriptions as ``.txt``, and Salesforce source units copied verbatim.
  - ``graph.json`` : lean structure. Every content-bearing node carries a ``content``
    pointer (a path relative to the bundle root) instead of inline body text, plus a
    short ``excerpt`` for Confluence pages. Under the no-DB constraint the graph IS
    the retrieval index — following edges gives structural recall a flat dump can't.

:func:`build_bundle` builds each source's graph from its source input, externalises
the content, joins every present pair (page->SF / issue->SF ``documents``;
issue<->page ``links-to``), merges into one graph, and writes
``<out>/{manifest.json, graph.json, content/, README.txt}`` plus a zip.

Confidentiality: a bundle holds real page bodies, issue text AND Salesforce
source — it is sensitive by default. Keep it local (gitignored); never commit or egress it. Only
source files that produced graph nodes are copied, so leakage-prone types nothing
graphs (Named Credentials, Static Resources) are never bundled.
"""
from __future__ import annotations

import json
import re
import shutil
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .confluence.join import join, merge
from .jira.join import join as jira_join, join_confluence
from .extractors import all_extractors, build_graph
from .persistence import to_json

SCHEMA_VERSION = 1
_EXCERPT_CHARS = 280


def _safe(name: str) -> str:
    """Filesystem-safe path segment (no traversal / odd chars)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "_"


# --------------------------------------------------------------------------- #
# Salesforce: map graphed nodes -> their source unit, then copy + point
# --------------------------------------------------------------------------- #
def _source_unit(path: Path) -> Path:
    """The on-disk unit to copy for a graphed file. Objects are folder-parsed and
    LWC/Aura are bundles, so map those to their folder; everything else is the file."""
    if path.name.endswith(".object-meta.xml"):
        return path.parent                                  # objects/<Name>/
    if path.parent.parent.name in ("lwc", "aura") and path.stem == path.parent.name:
        return path.parent                                  # lwc/<name>/ or aura/<name>/
    return path


def _safe_handles(ext, path) -> bool:
    try:
        return bool(ext.handles(path))
    except Exception:
        return False


def provenance(repo) -> dict:
    """``{node_id: source_unit_relpath}`` for every node a file in ``repo`` produces.

    Reuses the extractor registry + each file's handling extractor (the extract half
    of the core). Tolerant: a file no extractor handles, or an extractor that throws,
    contributes nothing. First writer wins (so an object node maps to its folder, not
    a later sibling file)."""
    repo = Path(repo)
    extractors = all_extractors()
    out: dict[str, str] = {}
    for path in sorted(p for p in repo.rglob("*") if p.is_file()):
        ext = next((e for e in extractors if _safe_handles(e, path)), None)
        if ext is None:
            continue
        try:
            nodes, _edges = ext.extract(path)
        except Exception:
            continue
        try:
            rel = str(_source_unit(path).relative_to(repo))
        except ValueError:
            continue
        for n in nodes or []:
            nid = n.get("id") if isinstance(n, dict) else None
            if nid:
                out.setdefault(nid, rel)
    return out


def _copy_unit(src: Path, dst: Path):
    """Copy a file or directory unit, creating parents. Tolerant of a missing src."""
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    elif src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def externalize_salesforce(sf_graph, force_app, content_dir) -> dict:
    """Copy each referenced SF source unit into ``content/salesforce/<relpath>`` and
    set ``node['content']`` on every non-external node mapped to a unit."""
    force_app = Path(force_app)
    prov = provenance(force_app)
    sf_root = Path(content_dir) / "salesforce"
    copied: set[str] = set()
    pointed = 0
    for n in sf_graph.get("nodes", []) or []:
        if not isinstance(n, dict) or n.get("external"):
            continue
        rel = prov.get(n.get("id"))
        if not rel:
            continue
        n["content"] = f"content/salesforce/{rel}"
        pointed += 1
        if rel not in copied:
            _copy_unit(force_app / rel, sf_root / rel)
            copied.add(rel)
    return {"units": len(copied), "nodes_pointed": pointed}


# --------------------------------------------------------------------------- #
# Confluence: write txt + xhtml, point + excerpt, drop inline text
# --------------------------------------------------------------------------- #
def _storage_by_page_id(dump_dir) -> dict:
    """``{page_id: raw_storage_xhtml}`` scanned from a Confluence dump."""
    out = {}
    for p in Path(dump_dir).rglob("*.page.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("id") is None:
            continue
        body = data.get("body") if isinstance(data.get("body"), dict) else {}
        storage = body.get("storage") if isinstance(body.get("storage"), dict) else {}
        out[str(data["id"])] = storage.get("value") or ""
    return out


def externalize_confluence(c_graph, dump_dir, content_dir) -> dict:
    """For each ``page`` node: write ``<pageId>.txt`` (body) + ``<pageId>.xhtml`` (raw
    storage) under ``content/confluence/<SPACE>/``, set ``content`` /
    ``content_xhtml`` / ``excerpt``, and DELETE the inline ``text``."""
    storage = _storage_by_page_id(dump_dir)
    base = Path(content_dir) / "confluence"
    pages = 0
    for n in c_graph.get("nodes", []) or []:
        if not isinstance(n, dict) or n.get("type") != "page" or n.get("external"):
            continue
        pid = str(n.get("page_id") or "")
        space = _safe(str(n.get("space_key") or "_"))
        stem = pid or _safe(str(n.get("label") or n.get("id") or "page"))
        body = n.get("text") or ""
        (base / space).mkdir(parents=True, exist_ok=True)
        (base / space / f"{stem}.txt").write_text(body, encoding="utf-8")
        n["content"] = f"content/confluence/{space}/{stem}.txt"
        xhtml = storage.get(pid, "")
        if xhtml:
            (base / space / f"{stem}.xhtml").write_text(xhtml, encoding="utf-8")
            n["content_xhtml"] = f"content/confluence/{space}/{stem}.xhtml"
        if body:
            n["excerpt"] = body[:_EXCERPT_CHARS]
        n.pop("text", None)
        pages += 1
    return {"pages": pages}


# --------------------------------------------------------------------------- #
# Jira: write txt, point + excerpt, drop inline text
# --------------------------------------------------------------------------- #
def externalize_jira(j_graph, content_dir) -> dict:
    """For each ``jiraissue`` node: write ``<KEY>.txt`` (summary + description)
    under ``content/jira/<PROJECT>/``, set ``content`` / ``excerpt``, and DELETE
    the inline ``text``."""
    base = Path(content_dir) / "jira"
    issues = 0
    for n in j_graph.get("nodes", []) or []:
        if not isinstance(n, dict) or n.get("type") != "jiraissue" or n.get("external"):
            continue
        key = _safe(str(n.get("id", "")).split("/", 1)[-1] or "issue")
        project = _safe(str(n.get("project_key") or "_"))
        body = "\n".join(x for x in (n.get("label"), n.get("text")) if x)
        (base / project).mkdir(parents=True, exist_ok=True)
        (base / project / f"{key}.txt").write_text(body, encoding="utf-8")
        n["content"] = f"content/jira/{project}/{key}.txt"
        if body:
            n["excerpt"] = body[:_EXCERPT_CHARS]
        n.pop("text", None)
        issues += 1
    return {"issues": issues}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _counts(graph) -> dict:
    return dict(Counter(
        n.get("type") for n in graph.get("nodes", []) or [] if isinstance(n, dict)))


def _zip_dir(src_dir: Path, zip_path: Path):
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(src_dir.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(src_dir))


def _readme(manifest) -> str:
    return (
        "graph-builder knowledge base\n"
        "============================\n\n"
        "Layout:\n"
        "  manifest.json  provenance, counts, schema version\n"
        "  graph.json     nodes (structure + `content` pointers + `excerpt`) + edges\n"
        "  content/       full source content the graph points into:\n"
        "    confluence/<SPACE>/<id>.txt    page body (plain text)\n"
        "    confluence/<SPACE>/<id>.xhtml  raw storage (tables, macros, diagram refs)\n"
        "    jira/<PROJECT>/<KEY>.txt       issue summary + description\n"
        "    salesforce/<path>              copied source units\n\n"
        "Consume:\n"
        "  1. load graph.json (nodes + edges).\n"
        "  2. find relevant nodes (match label/excerpt); expand via edges for\n"
        "     structural recall (links-to, child-of, documents, references, ...).\n"
        "  3. read a node's `content` path (relative to this bundle root) for full text.\n\n"
        "Retrieval is structural (graph) + lexical (text); there is no database and no\n"
        "semantic/vector search by design.\n\n"
        f"CONFIDENTIAL: {manifest['notice']}\n"
    )


def build_bundle(out_dir, *, salesforce=None, confluence_dump=None, jira_dump=None,
                 zip_path=None, join_opts=None, created_at=None, parallel=False) -> dict:
    """Build a knowledge-base bundle at ``out_dir`` (and, unless ``zip_path is False``,
    a zip).

    Provide any of ``salesforce`` (a force-app dir), ``confluence_dump`` (a dir of
    ``*.page.json`` from :func:`graphbuilder.confluence.collect`) and ``jira_dump``
    (a dir of ``*.issue.json`` from :func:`graphbuilder.jira.collect`). Builds each
    graph, externalises its content (pointers + files), joins every present pair
    (page->SF + issue->SF ``documents``; issue<->page ``links-to``), merges into
    one graph, and writes manifest.json / graph.json / content/ / README.txt.
    Returns a summary ``{out_dir, zip, manifest}``.
    """
    if not salesforce and not confluence_dump and not jira_dump:
        raise ValueError("build_bundle needs salesforce=, confluence_dump= and/or jira_dump=")
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)                  # clean rebuild — no stale content
    content_dir = out_dir / "content"
    content_dir.mkdir(parents=True, exist_ok=True)

    empty = {"nodes": [], "edges": [], "unresolved": [], "errors": []}
    sf_graph, c_graph, j_graph = dict(empty), dict(empty), dict(empty)
    sources: dict = {}

    # Build each source's graph. With `parallel`, offload the parser-free Confluence
    # and Jira builds to workers while the Salesforce build runs on THIS thread — the
    # Apex tree-sitter parser is unsendable (pinned to its origin thread), so the SF
    # build must not move off-thread. Overlap is modest (GIL); true multi-core CPU
    # parallelism (per-file multiprocessing) is deferred. Externalize/join/merge stay
    # serial and the merge order is fixed, so the result is identical regardless.
    others = [d for d in (confluence_dump, jira_dump) if d]
    if parallel and salesforce and others:
        with ThreadPoolExecutor(max_workers=len(others)) as ex:
            futures = {d: ex.submit(build_graph, d) for d in others}  # parser-free
            sf_graph = build_graph(salesforce)              # main thread (Apex parser)
            if confluence_dump:
                c_graph = futures[confluence_dump].result()
            if jira_dump:
                j_graph = futures[jira_dump].result()
    else:
        if salesforce:
            sf_graph = build_graph(salesforce)
        if confluence_dump:
            c_graph = build_graph(confluence_dump)
        if jira_dump:
            j_graph = build_graph(jira_dump)

    if salesforce:
        stats = externalize_salesforce(sf_graph, salesforce, content_dir)
        sources["salesforce"] = {"root": str(salesforce), "node_counts": _counts(sf_graph), **stats}
    if confluence_dump:
        stats = externalize_confluence(c_graph, confluence_dump, content_dir)
        spaces = sorted({
            n.get("space_key") for n in c_graph["nodes"]
            if isinstance(n, dict) and n.get("type") == "page" and n.get("space_key")
        })
        sources["confluence"] = {"dump": str(confluence_dump), "spaces": spaces, **stats}
    if jira_dump:
        stats = externalize_jira(j_graph, content_dir)
        projects = sorted({
            n.get("project_key") for n in j_graph["nodes"]
            if isinstance(n, dict) and n.get("type") == "jiraissue" and n.get("project_key")
        })
        sources["jira"] = {"dump": str(jira_dump), "projects": projects, **stats}

    # Cross-source joins for every pair that is present (each deliberate, tagged).
    cross = []
    if salesforce and confluence_dump:
        cross += join(c_graph, sf_graph, **(join_opts or {}))
    if salesforce and jira_dump:
        cross += jira_join(j_graph, sf_graph)
    if confluence_dump and jira_dump:
        cross += join_confluence(j_graph, c_graph)
    graph = merge(merge(sf_graph, c_graph), j_graph, cross)

    # Bodies were already externalised to content/ pointers; redact_text is the
    # belt-and-braces guarantee that no inline body can ever reach graph.json.
    (out_dir / "graph.json").write_text(to_json(graph, redact_text=True), encoding="utf-8")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generator": "graph-builder/bundle",
        "created_at": created_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": sources,
        "graph": {
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
            "documents_edges": sum(1 for e in graph["edges"] if e.get("type") == "documents"),
            "unresolved": len(graph.get("unresolved", [])),
            "errors": len(graph.get("errors", [])),
        },
        "content_format": {"confluence": ["txt", "xhtml"], "jira": ["txt"],
                           "salesforce": ["source"]},
        "notice": "Contains page bodies and Salesforce source. Keep local; do not commit or egress.",
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    (out_dir / "README.txt").write_text(_readme(manifest), encoding="utf-8")

    zpath = None
    if zip_path is not False:
        zpath = Path(zip_path) if zip_path else out_dir.parent / (out_dir.name + ".zip")
        _zip_dir(out_dir, zpath)

    return {"out_dir": str(out_dir), "zip": str(zpath) if zpath else None, "manifest": manifest}
