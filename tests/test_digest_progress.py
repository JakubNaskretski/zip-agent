"""Long-operations protocol: the ``progress`` kwarg on the ingest entry points
(MASTER_PROMPT §4 "Long operations") — narration fires every N files/KUs and
chunked extraction is behavior-identical to the single-call path.

Also covers:
- ``dg=`` pre-parsed digest skip (Change 2: no-double-parse split)
- ``rebuild_indexes(progress=)`` narration (Change 3)
"""
import pytest

from librarian import Librarian, Store, rebuild_indexes
from librarian.index import build_index
from librarian.digest import _progress, graphbuilder as sf, jira, confluence, office

from test_digest_graphbuilder import make_force_app
from test_digest_jira import make_jira_dump
from test_digest_confluence import make_confluence_dump
from test_digest_office import make_docs_dir


def test_progress_none_is_default_and_identical(tmp_path):
    """progress=None (the default) keeps the digest byte-identical to the
    chunked path — chunking only changes narration, never results."""
    dump = make_jira_dump(tmp_path)
    plain = jira.parse_jira(dump)
    lines = []
    chunked = jira.parse_jira(dump, progress=lines.append)
    assert [i.key for i in chunked.issues] == [i.key for i in plain.issues]
    assert chunked.summary() == plain.summary()
    assert chunked.graph == plain.graph


def test_progress_fires_during_parse(tmp_path, monkeypatch):
    monkeypatch.setattr(_progress, "EVERY", 2)   # 3 dump files -> 2 chunks
    lines = []
    jira.parse_jira(make_jira_dump(tmp_path), progress=lines.append)
    # first chunk: 2/3 files (intermediate); final chunk: "done — 3/3 …"
    assert lines == [
        "jira parse: 2/3 files scanned, 2 handled",
        "jira parse: done — 3/3 files scanned, 3 handled",
    ]


def test_progress_fires_during_ingest_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(_progress, "EVERY", 2)
    lib = Librarian(Store(tmp_path / "mem"))
    lines = []
    rep, d = jira.ingest_jira(lib, make_jira_dump(tmp_path), "dev",
                              "ingest sample Jira dump with narration",
                              progress=lines.append)
    assert rep.ok and len(d.issues) == 3
    # 3 issue KUs + 1 graph KU = 4 total; ticks at 2 and 4 (exact multiple)
    assert "jira ingest: 2 KUs staged" in lines
    assert "jira ingest: 4 KUs staged" in lines
    # count==4 is an exact multiple of EVERY=2, so done() is a no-op on the
    # ingest staging loop (no "jira ingest: done …" line)
    ingest_done = [ln for ln in lines if ln.startswith("jira ingest") and "done" in ln]
    assert ingest_done == []


def test_sf_digest_progress_smoke(tmp_path, monkeypatch):
    """The SF adapter narrates its extraction loop too, and the digest result
    matches the silent run."""
    monkeypatch.setattr(_progress, "EVERY", 3)
    fa = make_force_app(tmp_path)
    plain = sf.digest(fa)
    lines = []
    dg = sf.digest(fa, progress=lines.append)
    assert lines and all(ln.startswith("sf digest: ") for ln in lines)
    # the final line contains "done — N/N files scanned"
    final = lines[-1]
    assert "done —" in final
    # N/N portion: split on "done — " then first word is "N/N"
    counts = final.split("done — ")[1].split(" ")[0]
    done_n, total_n = counts.split("/")
    assert done_n == total_n                   # the last line covers every file
    assert dg.summary() == plain.summary()


# --------------------------------------------------------------------------- #
# dg= pre-parsed digest split (no-double-parse)
# --------------------------------------------------------------------------- #
def test_jira_ingest_with_precomputed_dg_skips_reparse(tmp_path, monkeypatch):
    """Passing dg= bypasses parse_jira; the Report/manifest state is identical
    to the normal (re-parse) path."""
    lib_normal = Librarian(Store(tmp_path / "mem_normal"))
    lib_precomputed = Librarian(Store(tmp_path / "mem_precomputed"))
    dump = make_jira_dump(tmp_path)

    rep_normal, d_normal = jira.ingest_jira(lib_normal, dump, "dev",
                                            "normal ingest path")

    # monkeypatch parse_jira to raise — must not be called when dg= is given
    precomputed_dg = jira.parse_jira(dump)
    monkeypatch.setattr(jira, "parse_jira", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("parse_jira was called even though dg= was provided")))
    rep_pre, d_pre = jira.ingest_jira(lib_precomputed, dump, "dev",
                                      "ingest with pre-parsed digest", dg=precomputed_dg)

    assert rep_pre.ok
    assert d_pre.summary() == d_normal.summary()
    # same KU set in both libraries
    normal_ids = {ku.id for ku in lib_normal.manifest.all() if ku.source == "jira"}
    pre_ids = {ku.id for ku in lib_precomputed.manifest.all() if ku.source == "jira"}
    assert normal_ids == pre_ids


def test_confluence_ingest_with_precomputed_dg_skips_reparse(tmp_path, monkeypatch):
    """Confluence: dg= produces the same manifest state without calling parse."""
    lib_normal = Librarian(Store(tmp_path / "mem_normal"))
    lib_precomputed = Librarian(Store(tmp_path / "mem_precomputed"))
    dump = make_confluence_dump(tmp_path)

    jira.ingest_jira  # just ensure it's not shadowing anything — no-op reference
    rep_normal, d_normal = confluence.ingest_confluence(lib_normal, dump, "dev",
                                                        "normal ingest path")

    precomputed_dg = confluence.parse_confluence(dump)
    monkeypatch.setattr(confluence, "parse_confluence",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("parse called with dg= provided")))
    rep_pre, d_pre = confluence.ingest_confluence(
        lib_precomputed, dump, "dev", "ingest with pre-parsed digest",
        dg=precomputed_dg)

    assert rep_pre.ok
    assert d_pre.summary() == d_normal.summary()
    normal_ids = {ku.id for ku in lib_normal.manifest.all() if ku.source == "confluence"}
    pre_ids = {ku.id for ku in lib_precomputed.manifest.all() if ku.source == "confluence"}
    assert normal_ids == pre_ids


def test_office_ingest_with_precomputed_dg_skips_reparse(tmp_path, monkeypatch):
    """Office: dg= produces the same manifest state without calling parse."""
    lib_normal = Librarian(Store(tmp_path / "mem_normal"))
    lib_precomputed = Librarian(Store(tmp_path / "mem_precomputed"))
    docs = make_docs_dir(tmp_path)

    rep_normal, d_normal = office.ingest_office(lib_normal, docs, "dev",
                                                "normal ingest path")

    precomputed_dg = office.parse_office(docs)
    monkeypatch.setattr(office, "parse_office",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("parse called with dg= provided")))
    rep_pre, d_pre = office.ingest_office(
        lib_precomputed, docs, "dev", "ingest with pre-parsed digest",
        dg=precomputed_dg)

    assert rep_pre.ok
    assert d_pre.summary() == d_normal.summary()
    normal_ids = {ku.id for ku in lib_normal.manifest.all() if ku.source == "docs"}
    pre_ids = {ku.id for ku in lib_precomputed.manifest.all() if ku.source == "docs"}
    assert normal_ids == pre_ids


def test_sf_ingest_with_precomputed_dg_skips_reparse(tmp_path, monkeypatch):
    """SF: dg= produces the same report + manifest state without calling digest."""
    lib_normal = Librarian(Store(tmp_path / "mem_normal"))
    lib_precomputed = Librarian(Store(tmp_path / "mem_precomputed"))
    fa = make_force_app(tmp_path)

    rep_normal, dg_normal = sf.ingest_salesforce(lib_normal, fa, "dev",
                                                 "normal ingest path")

    precomputed_dg = sf.digest(fa)
    monkeypatch.setattr(sf, "digest",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("digest called with dg= provided")))
    rep_pre, dg_pre = sf.ingest_salesforce(lib_precomputed, fa, "dev",
                                           "ingest with pre-parsed digest",
                                           dg=precomputed_dg)

    assert rep_pre.ok
    assert dg_pre.summary() == dg_normal.summary()
    normal_ids = {ku.id for ku in lib_normal.manifest.all() if ku.source == "salesforce"}
    pre_ids = {ku.id for ku in lib_precomputed.manifest.all() if ku.source == "salesforce"}
    assert normal_ids == pre_ids


# --------------------------------------------------------------------------- #
# index-build progress narration
# --------------------------------------------------------------------------- #
# The search index is now built in memory at open time (build_index), where the
# progress narration lives; rebuild_indexes is a no-op kept for compatibility.
def test_build_index_progress_emits_final_line(tmp_path, monkeypatch):
    """build_index(progress=) emits a compact final line after indexing."""
    lib = Librarian(Store(tmp_path / "mem"))
    # ingest a few KUs so the loop has something to iterate
    dump = make_jira_dump(tmp_path)
    jira.ingest_jira(lib, dump, "dev", "ingest sample Jira dump")

    lines: list = []
    build_index(lib, progress=lines.append)
    assert lines, "progress produced no output"
    # there should be at least one line containing "index rebuild"
    assert any("index rebuild" in ln for ln in lines)
    # the final line is either "index rebuild: done — N KUs indexed" (< EVERY)
    # or "index rebuild: N KUs indexed" (exact multiple) — either way it ends
    # with "indexed"
    assert lines[-1].endswith("indexed")


def test_build_index_progress_none_is_silent(tmp_path):
    """progress=None (the default) never calls any callback."""
    lib = Librarian(Store(tmp_path / "mem"))
    dump = make_jira_dump(tmp_path)
    jira.ingest_jira(lib, dump, "dev", "ingest sample Jira dump")
    # should not raise and returns a MemIndex
    mi = build_index(lib)
    assert mi.N > 0


def test_rebuild_indexes_is_a_silent_noop(tmp_path):
    """rebuild_indexes is a no-op kept for compatibility: returns an ok Report,
    never narrates progress."""
    lib = Librarian(Store(tmp_path / "mem"))
    dump = make_jira_dump(tmp_path)
    jira.ingest_jira(lib, dump, "dev", "ingest sample Jira dump")
    lines: list = []
    rep = rebuild_indexes(lib, "dev", "rebuild indexes (no-op)", progress=lines.append)
    assert rep.ok
    assert lines == []                          # nothing to narrate — no-op
