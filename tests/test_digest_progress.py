"""Long-operations protocol: the ``progress`` kwarg on the ingest entry points
(MASTER_PROMPT §4 "Long operations") — narration fires every N files/KUs and
chunked extraction is behavior-identical to the single-call path.
"""
from librarian import Librarian, Store
from librarian.digest import _progress, graphbuilder as sf, jira

from test_digest_graphbuilder import make_force_app
from test_digest_jira import make_jira_dump


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
    assert lines == [
        "jira parse: 2/3 files scanned, 2 handled",
        "jira parse: 3/3 files scanned, 3 handled",
    ]


def test_progress_fires_during_ingest_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(_progress, "EVERY", 2)
    lib = Librarian(Store(tmp_path / "mem"))
    lines = []
    rep, d = jira.ingest_jira(lib, make_jira_dump(tmp_path), "dev",
                              "ingest sample Jira dump with narration",
                              progress=lines.append)
    assert rep.ok and len(d.issues) == 3
    # 3 issue KUs + 1 graph KU -> staging ticks at 2 and 4
    assert "jira ingest: 2 KUs staged" in lines
    assert "jira ingest: 4 KUs staged" in lines


def test_sf_digest_progress_smoke(tmp_path, monkeypatch):
    """The SF adapter narrates its extraction loop too, and the digest result
    matches the silent run."""
    monkeypatch.setattr(_progress, "EVERY", 3)
    fa = make_force_app(tmp_path)
    plain = sf.digest(fa)
    lines = []
    dg = sf.digest(fa, progress=lines.append)
    assert lines and all(ln.startswith("sf digest: ") for ln in lines)
    done, total = lines[-1].removeprefix("sf digest: ").split(" ")[0].split("/")
    assert done == total                       # the last line covers every file
    assert dg.summary() == plain.summary()
