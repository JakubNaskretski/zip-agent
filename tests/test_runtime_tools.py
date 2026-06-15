"""Lightweight runtime — the RFP-support tools: search, workbook, notes, plan.

All read/write through a Workspace (no held engine, single-file writes). Reuses
the office fixture for a real .docx (heading "Overview") and .xlsx (sheet "Dane").
"""
from runtime import docs, layout, navigate, plan, search, work
from runtime.ingest import digest_to_tree
from runtime.storage import Workspace

import tests.test_digest_office as office_t


def _docs_ws(tmp_path):
    src = office_t.make_docs_dir(tmp_path / "src")
    ws = Workspace(None, str(tmp_path / "work"))
    digest_to_tree(ws, "docs", str(src))
    return ws


# -- on-demand text search over the sidecars -------------------------------- #
def test_search_hits_document_text(tmp_path):
    ws = _docs_ws(tmp_path)
    hits = search.search(ws, "Overview", source="docs")
    assert hits, "search found nothing for a known heading"
    assert hits[0]["source"] == "docs"
    assert "Overview" in hits[0]["snippet"]
    # an absent term returns nothing (no fuzzy guess)
    assert search.search(ws, "zzzznotpresentzzzz", source="docs") == []
    # the L0 map must advertise the SAME surface search actually scans (no phantom path)
    assert "kb/text/" not in ws.read_text(layout.INDEX_L0)


# -- workbook cells (the data the index deliberately omits) ----------------- #
def test_read_workbook_cells(tmp_path):
    ws = _docs_ws(tmp_path)
    wb = docs.read_workbook(ws, "mapping/dane.xlsx")
    assert "Dane" in wb
    flat = [c for row in wb["Dane"] for c in row]
    assert "Punkt pomiarowy" in flat and "42.5" in flat

    table = docs.read_table(ws, "mapping/dane.xlsx", "Dane")
    assert table["headers"] == ["Punkt pomiarowy", "Wartość nominalna"]
    assert table["rows"][0][0] == "MP-A1"


# -- curated notes as files, with stale-source detection -------------------- #
def test_notes_write_read_and_review(tmp_path):
    ws = _docs_ws(tmp_path)
    src = "kb/raw/docs/specs/Acme Integration Spec.docx"

    path = work.write_note(ws, "rfp/acme/req-001", "Bulk import is met by X.",
                           title="Req 1", derived_from=[src])
    assert path.endswith(".md")
    note = work.read_note(ws, "rfp/acme/req-001")
    assert note["frontmatter"]["title"] == "Req 1"
    assert "Bulk import" in note["body"]
    assert work.list_notes(ws, "rfp/acme")

    # fresh: nothing to review
    assert work.review(ws)["stale"] == []
    # the source changes underneath the note -> it is flagged
    ws.write_bytes(src, b"PK\x03\x04 different bytes")
    flagged = work.review(ws)["stale"]
    assert flagged and flagged[0]["note"] == path and src in flagged[0]["changed"]


# -- durable worklist survives a reset (just re-load the file) -------------- #
def test_plan_create_pending_mark_idempotent(tmp_path):
    ws = Workspace(None, str(tmp_path / "work"))
    plan.create_plan(ws, "acme-rfp", ["req-001", "req-002", "req-003"])
    assert plan.pending(ws, "acme-rfp") == ["req-001", "req-002", "req-003"]

    plan.mark(ws, "acme-rfp", "req-001")
    assert "req-001" not in plan.pending(ws, "acme-rfp")
    assert plan.progress(ws, "acme-rfp") == {
        "done": 1, "total": 3, "pending": ["req-002", "req-003"]}

    # re-create (e.g. after a reset) preserves progress, appends only new items
    plan.create_plan(ws, "acme-rfp", ["req-001", "req-004"])
    prog = plan.progress(ws, "acme-rfp")
    assert prog["done"] == 1 and prog["total"] == 4
    assert "req-001" not in prog["pending"] and "req-004" in prog["pending"]


# -- excerpt aligns the window with the match (casefold isn't length-preserving) -- #
def test_excerpt_survives_casefold_length_change():
    text = "ß" * 300 + " TARGETWORD sits well past the casefold drift."
    windows = navigate.excerpt(text, "TARGETWORD")
    assert windows and "TARGETWORD" in windows[0]


# -- a malformed/hand-written note never crashes read_note or the staleness scan -- #
def test_notes_tolerate_malformed_frontmatter(tmp_path):
    ws = Workspace(None, str(tmp_path / "work"))
    ws.write_text("kb/work/rfp/run/raw.md", "---\nnot json, no closing fence\nbody text")
    note = work.read_note(ws, "rfp/run/raw")
    assert note["frontmatter"] == {} and "body text" in note["body"]
    assert work.review(ws)["stale"] == []         # the scan must not raise
