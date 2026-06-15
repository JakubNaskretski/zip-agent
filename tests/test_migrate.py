"""Carry an existing Librarian-model KB onto the lightweight runtime.

Builds a real old-model zip (Store + Librarian + a Salesforce ingest + a curated
note), migrates it with no re-parse, and boots the lean result — proving the
knowledge (raw files, graph, curated notes) survives the structural change.
"""
import zipfile

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
