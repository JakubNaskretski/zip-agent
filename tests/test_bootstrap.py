"""Phase 2: the session entry, auto-checkpoint, and the deployable artifact."""
import sys

from librarian import boot, pack_zip, Store, Librarian
from factories import jira_ku


def test_boot_fresh_then_checkpoint_creates_zip(tmp_path):
    memzip = tmp_path / "memory.zip"
    session = boot(memzip, work_dir=tmp_path / "work")    # no zip yet
    assert not memzip.exists()
    session.begin("dev", "ingest the first issue").add_ku(jira_ku(1), body="x").commit()
    # autosave wrote the ZIP on commit
    assert memzip.exists()
    assert session.last_checkpoint_generation == 1


def test_autocheckpoint_persists_across_boots(tmp_path):
    memzip = tmp_path / "memory.zip"
    s1 = boot(memzip, work_dir=tmp_path / "w1")
    s1.begin("dev", "ingest issue one").add_ku(jira_ku(1), body="a").commit()
    s1.begin("dev", "ingest issue two").add_ku(jira_ku(2), body="b").commit()

    # a brand-new session over the retained ZIP sees everything
    s2 = boot(memzip, work_dir=tmp_path / "w2")
    assert s2.stats()["total"] == 2
    assert s2.librarian.manifest.generation == 2


def test_noop_commit_does_not_recheckpoint(tmp_path):
    memzip = tmp_path / "memory.zip"
    s = boot(memzip, work_dir=tmp_path / "w")
    s.begin("dev", "ingest issue one").ingest_ku(jira_ku(1), body="same").commit()
    gen = s.last_checkpoint_generation
    s.begin("dev", "re-ingest identical content").ingest_ku(jira_ku(1), body="same").commit()
    assert s.last_checkpoint_generation == gen     # no re-pack on a no-op


def test_export_is_independent_of_autosave(tmp_path):
    memzip = tmp_path / "memory.zip"
    s = boot(memzip, work_dir=tmp_path / "w")
    s.begin("dev", "ingest issue one").add_ku(jira_ku(1), body="x").commit()
    out = s.export(tmp_path / "handoff.zip")
    assert out.exists() and out != memzip


def test_deployable_zip_carries_engine_and_boots(tmp_path):
    """The build helper produces a ZIP that contains the librarian engine and
    boots into a working session. The master prompt is deliberately NOT bundled
    (it's pasted into the agent builder's instructions field)."""
    from scripts.build_memory import build

    memzip = build(tmp_path / "memory.zip")
    import zipfile
    with zipfile.ZipFile(memzip) as zf:
        names = zf.namelist()
    assert any(n.startswith("librarian/") and n.endswith("librarian.py") for n in names)
    assert "MASTER_PROMPT.md" not in names and "AGENT.md" not in names

    session = boot(memzip, work_dir=tmp_path / "deployed")
    session.begin("dev", "first ingest on the deployed artifact").add_ku(jira_ku(7), body="z").commit()
    assert session.get("jira:PROJ-7") is not None
    # the unpacked engine is importable from inside the ZIP
    assert str(tmp_path / "deployed") in sys.path
