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


def test_wheelhouse_skips_pip_when_ast_stack_already_importable(tmp_path, monkeypatch):
    """Re-boot guard: if tree_sitter + tree_sitter_language_pack already import,
    _install_wheelhouse must not invoke pip at all."""
    import subprocess
    import types
    from librarian.bootstrap import _install_wheelhouse

    wh = tmp_path / "reference" / "wheelhouse"
    wh.mkdir(parents=True)
    (wh / "tree_sitter-0.25.2-cp310-abi3-manylinux2014_x86_64.whl").write_bytes(b"x")
    monkeypatch.setitem(sys.modules, "tree_sitter", types.ModuleType("tree_sitter"))
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack",
                        types.ModuleType("tree_sitter_language_pack"))

    def _no_pip(*a, **kw):
        raise AssertionError("pip must not run when the AST stack already imports")
    monkeypatch.setattr(subprocess, "run", _no_pip)

    assert _install_wheelhouse(tmp_path) == {"installed": True,
                                             "skipped": "already importable"}


def test_wheelhouse_still_installs_when_ast_stack_missing(tmp_path, monkeypatch):
    """With the probe failing, the existing pip flow runs unchanged."""
    import subprocess
    import types
    from librarian.bootstrap import _install_wheelhouse

    wh = tmp_path / "reference" / "wheelhouse"
    wh.mkdir(parents=True)
    (wh / "tree_sitter-0.25.2-cp310-abi3-manylinux2014_x86_64.whl").write_bytes(b"x")
    # None entries make `import tree_sitter` raise ImportError deterministically
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", None)

    calls = []
    def _fake_pip(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", _fake_pip)

    assert _install_wheelhouse(tmp_path) == {"installed": True, "count": 1}
    assert len(calls) == 1 and "--no-index" in calls[0]


def test_build_slims_language_pack_wheel(tmp_path):
    """The fat 0.x language-pack wheel is stripped to the apex grammar with a
    valid regenerated RECORD; other wheels pass through untouched."""
    import base64, hashlib, zipfile as zf
    import importlib.util
    from pathlib import Path as _P
    spec = importlib.util.spec_from_file_location(
        "build_memory", _P(__file__).resolve().parents[1] / "scripts" / "build_memory.py")
    bm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bm)

    wh = tmp_path / "wh"
    wh.mkdir()
    pack = wh / "tree_sitter_language_pack-0.13.0-cp310-abi3-manylinux2014_x86_64.whl"
    with zf.ZipFile(pack, "w") as z:
        meta = b"Metadata-Version: 2.1\nName: tree-sitter-language-pack\nVersion: 0.13.0\n"
        z.writestr("tree_sitter_language_pack/__init__.py", "x = 1\n")
        z.writestr("tree_sitter_language_pack/bindings/apex.abi3.so", b"\x7fELF-apex")
        z.writestr("tree_sitter_language_pack/bindings/verilog.abi3.so", b"\x7fELF" + b"0" * 4096)
        z.writestr("tree_sitter_language_pack-0.13.0.dist-info/METADATA", meta)
        z.writestr("tree_sitter_language_pack-0.13.0.dist-info/RECORD", "stale\n")
    (wh / "tree_sitter-0.25.2-cp312-cp312-manylinux2014_x86_64.whl").write_bytes(
        zf.ZipFile.__name__.encode())  # passthrough marker file (not opened by build)

    memzip = bm.build(tmp_path / "m.zip", wheelhouse=wh)
    with zf.ZipFile(memzip) as z:
        slim_name = "reference/wheelhouse/" + pack.name
        data = z.read(slim_name)
    with zf.ZipFile(__import__("io").BytesIO(data)) as sw:
        names = sw.namelist()
        assert "tree_sitter_language_pack/bindings/apex.abi3.so" in names
        assert not any("verilog" in n for n in names)
        record = sw.read("tree_sitter_language_pack-0.13.0.dist-info/RECORD").decode()
        # every kept file is hash-listed; the stale RECORD line is gone
        assert "apex.abi3.so" in record and "stale" not in record
        h = base64.urlsafe_b64encode(
            hashlib.sha256(b"\x7fELF-apex").digest()).rstrip(b"=").decode()
        assert h in record
