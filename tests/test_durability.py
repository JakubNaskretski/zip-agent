"""The ZIP-in / ZIP-out memory cycle and the atomic swap (I12)."""
import zipfile

from librarian import Librarian, Store, pack_zip, unpack_zip
from factories import jira_ku, curated_ku


def test_zip_roundtrip_through_librarian(tmp_path):
    # session 1: build memory in a working dir, commit, pack to a ZIP
    work1 = tmp_path / "work1"
    lib = Librarian(Store(work1))
    lib.begin("dev", "ingest first batch") \
        .add_ku(jira_ku(1), body="hello") \
        .add_ku(curated_ku(derived_from="jira:PROJ-1"), body="note").commit()
    memzip = pack_zip(work1, tmp_path / "memory.zip")
    assert zipfile.is_zipfile(memzip)

    # session 2: a fresh sandbox unpacks the retained ZIP and sees everything
    store2 = unpack_zip(memzip, tmp_path / "work2")
    lib2 = Librarian(store2)
    assert lib2.get("jira:PROJ-1").title == "Issue 1"
    assert lib2.get("curated:mappings/meter-map").links[0]["to"] == "jira:PROJ-1"
    assert lib2.manifest.generation == 1
    assert len(lib2.changelog.entries) == 1


def test_pack_is_atomic_and_excludes_tmp(tmp_path):
    work = tmp_path / "work"
    lib = Librarian(Store(work))
    lib.begin("dev", "ingest one ticket").add_ku(jira_ku(1), body="x").commit()
    # leave a stray scratch file behind; it must not end up in the archive
    (work / "stray.tmp").write_text("scratch")
    memzip = pack_zip(work, tmp_path / "memory.zip")
    with zipfile.ZipFile(memzip) as zf:
        names = zf.namelist()
    assert "manifest.json" in names
    assert "kb/raw/jira/PROJ-1.json" in names
    assert not any(n.endswith(".tmp") for n in names)


def test_continued_edits_persist_across_zip_cycle(tmp_path):
    work1 = tmp_path / "w1"
    Librarian(Store(work1)).begin("dev", "ingest issue one").add_ku(jira_ku(1), body="a").commit()
    z = pack_zip(work1, tmp_path / "m.zip")

    store2 = unpack_zip(z, tmp_path / "w2")
    lib2 = Librarian(store2)
    lib2.begin("dev", "ingest a second issue").add_ku(jira_ku(2), body="b").commit()
    z2 = pack_zip(store2.root, tmp_path / "m2.zip")

    lib3 = Librarian(unpack_zip(z2, tmp_path / "w3"))
    assert lib3.stats()["total"] == 2
    assert lib3.manifest.generation == 2
