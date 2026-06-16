"""Carry an existing Librarian-model KB onto the lightweight runtime.

Builds a real old-model zip (Store + Librarian + a Salesforce ingest + a curated
note), migrates it with no re-parse, and boots the lean result — proving the
knowledge (raw files, graph, curated notes) survives the structural change.
"""
import zipfile

import pytest

from librarian import KnowledgeUnit, Librarian, Store
from librarian.digest import graphbuilder as sf
from librarian.store import pack_zip

from scripts.build_memory import migrate_to_lean
from runtime import boot, navigate

from tests.test_digest_graphbuilder import make_force_app


def _old_kb_zip(tmp_path):
    """A deployed-style Librarian zip: a Salesforce ingest + one curated note."""
    make_force_app(tmp_path)
    store = Store(tmp_path / "oldmem")
    lib = Librarian(store)
    sf.ingest_salesforce(lib, str(tmp_path / "force-app"), "dev",
                         "seed ingest for the migration test")
    note = KnowledgeUnit(
        id="curated:notes/keep", kind="curated-note", tier="curated", source="agent",
        path="kb/curated/notes/keep.md", title="A kept finding")
    lib.begin("dev", "save a curated finding to carry forward").add_ku(
        note, body="bulk import is handled by X").commit()
    return pack_zip(store.root, tmp_path / "old.zip")


def test_migrate_carries_kb_onto_lean_runtime(tmp_path):
    old_zip = _old_kb_zip(tmp_path)
    # confirm the OLD layout is what we expect to migrate from
    with zipfile.ZipFile(old_zip) as z:
        names = z.namelist()
    assert "kb/structured/salesforce/graph.json" in names      # old graph path
    assert any(n.startswith("kb/raw/salesforce/") for n in names)
    assert "kb/curated/notes/keep.md" in names

    out_dir, memzip, prompt_path, counts = migrate_to_lean(
        str(old_zip), "project", out_dir=tmp_path / "out")
    assert counts["graphs"] >= 1 and counts["raw"] > 0 and counts["curated"] >= 1

    # the migrated zip is the lean layout, KB carried, no re-parse
    with zipfile.ZipFile(memzip) as z:
        lean = z.namelist()
    assert "agent_manifest.json" in lean and "graph/salesforce.json" in lean
    assert "index/L0.md" in lean and "kb/structured/salesforce/graph.json" not in lean

    # boot the migrated zip and prove the knowledge is intact
    session = boot(str(memzip), str(tmp_path / "work"))
    assert "salesforce" in session.sources()
    g = session.shard("salesforce")
    assert any(n["id"] == "object/MeterPoint__c" for n in g["nodes"])
    # the curated note carried verbatim and is readable
    assert "bulk import is handled by X" in session.ws.read_text("kb/curated/notes/keep.md")
    # the verbatim source behind a node is still retrievable
    tnode = navigate.node(g, "trigger/MeterPointTrigger")
    assert navigate.read_source(session.ws, "salesforce", tnode) is not None
    # the regenerated L0 map reflects the carried graph
    assert "salesforce" in session.l0


def _rewrap(src_zip, dst_zip, prefix):
    """Re-pack every member of ``src_zip`` under ``prefix`` (e.g. a Finder/Explorer
    'compress folder' that nests everything under one directory)."""
    with zipfile.ZipFile(src_zip) as zin, zipfile.ZipFile(dst_zip, "w") as zout:
        for n in zin.namelist():
            if not n.endswith("/"):
                zout.writestr(prefix + n, zin.read(n))
    return dst_zip


def test_migrate_carries_kb_from_a_wrapper_folder_zip(tmp_path):
    # a zip whose KB sits under a single wrapper dir (memory/kb/raw/…) must still
    # carry — the literal kb/raw/ prefix match would otherwise find NOTHING and the
    # migrate would silently emit an empty agent.
    wrapped = _rewrap(_old_kb_zip(tmp_path), tmp_path / "wrapped.zip", "memory/")
    out_dir, memzip, _prompt, counts = migrate_to_lean(
        str(wrapped), "project", out_dir=tmp_path / "out")
    assert counts["raw"] > 0 and counts["graphs"] >= 1 and counts["curated"] >= 1

    with zipfile.ZipFile(memzip) as z:
        lean = z.namelist()
    assert "graph/salesforce.json" in lean                       # wrapper stripped, KB at root
    assert any(n.startswith("kb/raw/salesforce/") for n in lean)
    assert not any(n.startswith("memory/") for n in lean)        # no wrapper leaked through


def test_migrate_refuses_empty_output_on_unrecognised_layout(tmp_path):
    # a zip holding content under an unrecognised prefix must NOT yield a deploy-ready
    # empty memory.zip — it must abort loudly (so a real KB is never silently dropped).
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("knowledge/sources/big.bin", b"x" * 5000)
        z.writestr("knowledge/meta.json", "{}")
    out = tmp_path / "out"

    with pytest.raises(SystemExit) as e:
        migrate_to_lean(str(bad), "project", out_dir=out)
    assert "carried NOTHING" in str(e.value) and "knowledge" in str(e.value)
    assert not (out / "memory.zip").exists()                     # nothing written


def test_migrate_wrapper_zip_carries_curated_and_wheelhouse(tmp_path):
    # a real-world shape: curated knowledge (image-transcriptions + summaries) + a
    # graph + a bundled wheelhouse, ALL nested under one wrapper dir. Every piece must
    # carry — curated knowledge AND the offline wheels — not just kb/raw.
    src = tmp_path / "src.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("memory/kb/curated/image-transcriptions/img001.md", "transcribed text\n")
        z.writestr("memory/kb/curated/summaries/doc.md", "a summary\n")
        z.writestr("memory/graph/docs.json",
                   '{"version":1,"nodes":[],"edges":[],"unresolved":[],"errors":[]}')
        z.writestr("memory/reference/wheelhouse/foo-1.0-py3-none-any.whl", b"wheelbytes")
        z.writestr("memory/runtime/boot.py", "x")        # code — not carried
        z.writestr("memory/agent_manifest.json", "{}")

    _out, memzip, _p, counts = migrate_to_lean(str(src), "project", out_dir=tmp_path / "out")
    assert counts["curated"] == 2 and counts["graphs"] >= 1    # curated KNOWLEDGE carried

    with zipfile.ZipFile(memzip) as z:
        names = z.namelist()
    assert "kb/curated/image-transcriptions/img001.md" in names   # wrapper stripped
    assert "kb/curated/summaries/doc.md" in names
    assert any(n.endswith("foo-1.0-py3-none-any.whl") for n in names)  # wheelhouse carried
    assert not any(n.startswith("memory/") for n in names)            # no wrapper leaked
