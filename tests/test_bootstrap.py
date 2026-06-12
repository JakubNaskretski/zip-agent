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


def test_checkpoint_every_batches_packs_and_flushes_explicitly(tmp_path):
    """checkpoint_every=N packs only every Nth CHANGED commit; the explicit
    checkpoint() is the final flush for the trailing commits."""
    memzip = tmp_path / "memory.zip"
    s = boot(memzip, work_dir=tmp_path / "w", install_wheelhouse=False,
             checkpoint_every=3)
    s.begin("dev", "ingest issue one").add_ku(jira_ku(1), body="a").commit()
    s.begin("dev", "ingest issue two").add_ku(jira_ku(2), body="b").commit()
    assert not memzip.exists()                     # batched — not packed yet
    s.begin("dev", "ingest issue three").add_ku(jira_ku(3), body="c").commit()
    assert memzip.exists()                         # 3rd changed commit packs
    assert s.last_checkpoint_generation == 3

    # a no-op commit (I9) never advances the batch counter
    s.begin("dev", "re-ingest identical content").ingest_ku(jira_ku(3), body="c").commit()
    s.begin("dev", "ingest issue four").add_ku(jira_ku(4), body="d").commit()
    assert s.last_checkpoint_generation == 3       # 4th changed commit pending
    assert boot(memzip, work_dir=tmp_path / "peek", install_wheelhouse=False,
                autosave=False).stats()["total"] == 3

    s.checkpoint()                                 # the explicit final flush
    assert s.last_checkpoint_generation == 4
    assert boot(memzip, work_dir=tmp_path / "peek2", install_wheelhouse=False,
                autosave=False).stats()["total"] == 4


def test_checkpoint_every_default_keeps_per_commit_durability(tmp_path):
    memzip = tmp_path / "memory.zip"
    s = boot(memzip, work_dir=tmp_path / "w", install_wheelhouse=False)
    s.begin("dev", "ingest issue one").add_ku(jira_ku(1), body="a").commit()
    assert s.last_checkpoint_generation == 1       # packed immediately (default)


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


def test_build_bundles_wheelhouse(tmp_path):
    """--wheelhouse packs *.whl into reference/wheelhouse/ (where boot()'s
    offline installer already looks); an empty dir is a hard error, not a
    silently AST-less artifact."""
    from scripts.build_memory import build

    wh = tmp_path / "wh"
    wh.mkdir()
    (wh / "tree_sitter-0.23.0-cp39-abi3-manylinux2014_x86_64.whl").write_bytes(b"x")
    memzip = build(tmp_path / "memory.zip", wheelhouse=wh)
    import zipfile
    with zipfile.ZipFile(memzip) as zf:
        names = zf.namelist()
    assert any(n.startswith("reference/wheelhouse/") and n.endswith(".whl")
               for n in names)

    import pytest
    (tmp_path / "empty").mkdir()
    with pytest.raises(SystemExit):
        build(tmp_path / "m2.zip", wheelhouse=tmp_path / "empty")


def test_boot_discards_stale_workdir_for_newer_zip(tmp_path):
    """A freshly uploaded (newer) memory.zip supersedes an old unpack — boot
    must not reuse or mix the stale working dir."""
    import os, time
    from librarian.bootstrap import boot
    from librarian.store import pack_zip

    src = tmp_path / "src"
    (src / "kb").mkdir(parents=True)
    (src / "manifest.json").write_text('{"schema": 1, "generation": 0, "kus": []}')
    z = pack_zip(src, tmp_path / "memory.zip")
    work = tmp_path / "work"
    boot(z, work_dir=work, install_wheelhouse=False, autosave=False)
    stale = work / "leftover.tmp.txt"
    stale.write_text("old generation")
    # age the workdir, then ship a newer zip
    old = time.time() - 120
    os.utime(work / "manifest.json", (old, old))
    (src / "kb" / "new-marker.txt").write_text("v2")
    z = pack_zip(src, tmp_path / "memory.zip")
    boot(z, work_dir=work, install_wheelhouse=False, autosave=False)
    assert not stale.exists()                       # stale generation wiped
    assert (work / "kb" / "new-marker.txt").exists()  # new content unpacked


def test_boot_surfaces_wheelhouse_report(tmp_path):
    from librarian.bootstrap import boot
    from librarian.store import pack_zip

    src = tmp_path / "src"
    src.mkdir()
    (src / "manifest.json").write_text('{"schema": 1, "generation": 0, "kus": []}')
    z = pack_zip(src, tmp_path / "memory.zip")
    s = boot(z, work_dir=tmp_path / "w", install_wheelhouse=True, autosave=False)
    assert s.wheelhouse == {"installed": False, "reason": "no wheelhouse bundled"}
