"""Confluence digest (graph-builder-backed adapter) — synthetic collector dumps.

Fixture mirrors the collector layout (``<dump_dir>/<SPACE>/<id>.page.json``, one
Data Center REST ``content`` envelope per file): an overview page linking to a
child runbook page that carries an attachment. Reproducible without any real
Confluence; fictional Acme data only.
"""
import json

from librarian import Librarian, Store, rebuild_indexes, retrieve
from librarian.digest import confluence


def page_json(page_id, space, title, storage="", ancestors=(), labels=(),
              author="", version=3):
    data = {
        "id": page_id, "type": "page", "title": title,
        "space": {"key": space},
        "version": {"number": version},
        "ancestors": [{"id": i, "title": t} for i, t in ancestors],
        "body": {"storage": {"value": storage}},
        "metadata": {"labels": {"results": [{"name": l} for l in labels]}},
    }
    if author:
        data["version"]["by"] = {"userKey": author}
    return json.dumps(data)


def make_confluence_dump(root):
    space = root / "confluence-dump" / "ACME"
    space.mkdir(parents=True)
    (space / "1001.page.json").write_text(page_json(
        "1001", "ACME", "Billing Integration Overview",
        storage='<p>The nightly invoice export pushes batches through the '
                'syncBilling flow.</p>'
                '<ac:link><ri:page ri:content-title="Export Runbook" '
                'ri:space-key="ACME"/></ac:link>',
        labels=["integration"], author="jdoe"), "utf-8")
    (space / "1002.page.json").write_text(page_json(
        "1002", "ACME", "Export Runbook",
        storage='<p>Restart the export service after a failed run.</p>'
                '<ac:link><ri:attachment ri:filename="export-checklist.pdf"/>'
                '</ac:link>',
        ancestors=[("1001", "Billing Integration Overview")]), "utf-8")
    return root / "confluence-dump"


def test_parse_pages_and_summary(tmp_path):
    d = confluence.parse_confluence(make_confluence_dump(tmp_path))
    assert {p.page_id for p in d.pages} == {"1001", "1002"}
    assert all(p.space_key == "ACME" for p in d.pages)
    by_id = {p.page_id: p for p in d.pages}
    assert by_id["1001"].title == "Billing Integration Overview"
    assert by_id["1002"].version == 3
    assert d.errors == [] and d.unresolved == []
    s = d.summary()
    assert s["pages"] == 2 and s["spaces"] == 1
    # 2 pages + 1 space + 1 label + 1 user + 1 attachment
    assert s["nodes"] == 6
    # 1001: child-of space/links-to/labeled/authored-by = 4; 1002: child-of
    # parent page (id-resolved)/attaches = 2
    assert s["edges"] == 6


def test_ingest_creates_kus_and_graph(tmp_path):
    lib = Librarian(Store(tmp_path / "mem"))
    rep, d = confluence.ingest_confluence(lib, make_confluence_dump(tmp_path),
                                          "dev", "ingest sample Confluence dump")
    assert rep.ok
    ku = lib.get("confluence:ACME/1001")
    assert ku is not None and ku.tier == "raw" and ku.kind == "source-record"
    assert ku.title == "Billing Integration Overview"
    assert ku.path == "kb/raw/confluence/ACME/1001.json"
    assert ku.provenance == {
        "space_key": "ACME", "page_id": "1001", "version": 3,
        "content_type": "page", "source_path": "ACME/1001.page.json",
    }
    assert lib.get("confluence:ACME/1002") is not None
    gku = lib.get(confluence.GRAPH_ID)
    assert gku is not None and gku.tier == "structured" and gku.kind == "graph"
    g = confluence.load_graph(lib)
    assert len(g["nodes"]) == 6 and len(g["edges"]) == 6
    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    # the title-form link resolved to the id-keyed page node, not a stub
    assert ("page/1001", "links-to", "page/1002") in edges
    assert ("page/1002", "child-of", "page/1001") in edges


def test_entities_are_structured_ids_only(tmp_path):
    """HARD RULE: the entity bridge gets the space key + page id only — never
    the title or any prose-derived name."""
    lib = Librarian(Store(tmp_path / "mem"))
    confluence.ingest_confluence(lib, make_confluence_dump(tmp_path), "dev",
                                 "ingest sample Confluence dump")
    assert lib.get("confluence:ACME/1001").entities == ["ACME", "1001"]
    assert lib.get("confluence:ACME/1002").entities == ["ACME", "1002"]


def test_graph_body_redacts_page_text(tmp_path):
    """Page bodies live in the raw KUs; the stored graph JSON must carry no
    inline ``text`` attr — and no cross-source ``documents`` edges (no join)."""
    lib = Librarian(Store(tmp_path / "mem"))
    confluence.ingest_confluence(lib, make_confluence_dump(tmp_path), "dev",
                                 "ingest sample Confluence dump")
    stored = json.loads(lib.read_body(confluence.GRAPH_ID))
    assert all("text" not in n for n in stored["nodes"])
    redacted = {n["id"] for n in stored["nodes"] if n.get("text_redacted")}
    assert {"page/1001", "page/1002"} <= redacted    # both had body text
    assert all(e["type"] != "documents" for e in stored["edges"])


def test_reingest_unchanged_is_noop(tmp_path):
    lib = Librarian(Store(tmp_path / "mem"))
    dump = make_confluence_dump(tmp_path)
    confluence.ingest_confluence(lib, dump, "dev", "ingest sample Confluence dump")
    gen = lib.manifest.generation
    rep, _ = confluence.ingest_confluence(lib, dump, "dev",
                                          "re-ingest identical Confluence dump")
    assert rep.unchanged and lib.manifest.generation == gen


def test_fts_finds_page_by_body_word_and_read_body(tmp_path):
    """Confluence prose is reached via full-text search (never the entity
    bridge), and ``read_body`` returns the full raw page detail."""
    lib = Librarian(Store(tmp_path / "mem"))
    confluence.ingest_confluence(lib, make_confluence_dump(tmp_path), "dev",
                                 "ingest sample Confluence dump")
    rebuild_indexes(lib, "dev", "rebuild indexes after confluence digest")
    con = retrieve.open_index(lib)
    hits = {h["ku_id"] for h in retrieve.search(con, "syncBilling")}
    assert "confluence:ACME/1001" in hits
    detail = json.loads(lib.read_body("confluence:ACME/1001"))
    assert "nightly invoice export" in detail["body"]["storage"]["value"]
    # the title is NOT an entity — prose stays out of the bridge
    assert retrieve.find_entity(con, "Billing Integration Overview") == []
    assert {h["ku_id"] for h in retrieve.find_entity(con, "1001")} \
        == {"confluence:ACME/1001"}
