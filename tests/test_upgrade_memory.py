"""scripts/upgrade_memory.py — ship new code without losing knowledge.

The state/code split: kb/** + manifest.json + dev/ (changelog, session state)
come from the OLD zip; everything else comes from the NEW build; derived
indexes are dropped (rebuildable, I13) and rebuilt by the new engine.
"""
import hashlib
import json
import zipfile

import pytest

from librarian import Librarian, Store, boot, pack_zip, rebuild_indexes, retrieve
from factories import jira_ku, curated_ku
from scripts.upgrade_memory import upgrade


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_old(tmp_path):
    """A deployed memory.zip: a few KUs (one curated) and old code.

    No persisted index: on this branch the search index is built in memory at
    open time (rebuild_indexes is a no-op kept for compatibility), so the old
    zip never carries a kb/indexes/ blob."""
    work = tmp_path / "old_work"
    lib = Librarian(Store(work))
    lib.begin("dev", "seed knowledge for the upgrade test") \
        .add_ku(jira_ku(1), body="issue one body") \
        .add_ku(jira_ku(2), body="issue two body") \
        .add_ku(curated_ku(derived_from="jira:PROJ-1"), body="curated note").commit()
    rebuild_indexes(lib, "dev", "no-op rebuild (index is in memory)")   # no-op
    (work / "librarian").mkdir(exist_ok=True)
    (work / "librarian" / "OLD_CODE_MARKER.py").write_text("OLD = 1")
    return pack_zip(work, tmp_path / "old_memory.zip")


def _build_new(tmp_path, manifest_version="1.0"):
    """A fresh code build: engine + assets — plus stray state that must be ignored."""
    src = tmp_path / "new_src"
    (src / "librarian" / "digest").mkdir(parents=True)
    (src / "librarian" / "manifest.py").write_text(
        f'MANIFEST_VERSION = "{manifest_version}"\n')
    (src / "librarian" / "NEW_CODE_MARKER.py").write_text("NEW = 2")
    (src / "librarian" / "digest" / "graphbuilder.py").write_text(
        '_VENDORED_SHA = "abc1234"\n')
    (src / "graphbuilder").mkdir()
    (src / "graphbuilder" / "core.py").write_text("# vendored engine")
    (src / "reference" / "wheelhouse").mkdir(parents=True)
    (src / "reference" / "wheelhouse" / "pkg-1.0-py3-none-any.whl").write_bytes(b"w")
    # stray state in a code build must NOT leak into the upgrade
    (src / "kb" / "raw").mkdir(parents=True)
    (src / "kb" / "raw" / "leak.txt").write_text("must not survive")
    (src / "manifest.json").write_text('{"resources": [{"id": "leak"}]}')
    return pack_zip(src, tmp_path / "new_code.zip")


def test_state_from_old_code_from_new_indexes_dropped(tmp_path, capsys):
    old, new = _build_old(tmp_path), _build_new(tmp_path)
    old_sha, new_sha = _sha(old), _sha(new)

    out = upgrade(old, new, tmp_path / "upgraded.zip")

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))
        changelog = json.loads(zf.read("dev/changelog.json"))

    # STATE survived: every KB tier, manifest, changelog, session state.
    # The seed commit is the only changelog entry — rebuild_indexes is a no-op
    # now, so it adds neither an entry nor a generation bump.
    assert "kb/raw/jira/PROJ-1.json" in names
    assert "kb/raw/jira/PROJ-2.json" in names
    assert "kb/curated/mappings/meter-map.md" in names
    assert "dev/session_state.json" in names
    assert len(changelog["entries"]) == 1          # history carried verbatim

    # CODE swapped: new engine in, old engine out, assets bundled
    assert "librarian/NEW_CODE_MARKER.py" in names
    assert "librarian/OLD_CODE_MARKER.py" not in names
    assert "graphbuilder/core.py" in names
    assert "reference/wheelhouse/pkg-1.0-py3-none-any.whl" in names

    # No persisted index ever existed (built in memory now); none in the upgrade.
    assert not any(n.startswith("kb/indexes/") for n in names)
    ids = {r["id"] for r in manifest["resources"]}
    assert ids == {"jira:PROJ-1", "jira:PROJ-2", "curated:mappings/meter-map"}
    assert manifest["generation"] == 1             # rest of the manifest verbatim

    # NEW zip's stray state was ignored, not merged
    assert "kb/raw/leak.txt" not in names

    # inputs untouched
    assert _sha(old) == old_sha and _sha(new) == new_sha

    # the upgrade note explains the index is built in memory at open time
    out_text = capsys.readouterr().out
    assert "open_index" in out_text and "in memory" in out_text


def test_upgraded_zip_boots_and_search_is_live_at_open(tmp_path):
    out = upgrade(_build_old(tmp_path), _build_new(tmp_path), tmp_path / "up.zip")
    session = boot(out, work_dir=tmp_path / "deployed",
                   install_wheelhouse=False, autosave=False)
    lib = session.librarian
    assert lib.get("curated:mappings/meter-map") is not None
    assert lib.read_body("jira:PROJ-1") == b"issue one body"

    # search is live immediately — open_index builds the MemIndex from the live
    # KB; no rebuild step (and no persisted index) is needed
    con = retrieve.open_index(lib)
    assert retrieve.find_entity(con, "MeterPointService")


def test_refuses_old_manifest_newer_than_new_code(tmp_path):
    old = _build_old(tmp_path)
    # re-pack the old zip with a manifest from the future
    work = tmp_path / "old_work"
    m = json.loads((work / "manifest.json").read_text())
    m["manifest_version"] = "9.9"
    (work / "manifest.json").write_text(json.dumps(m))
    old = pack_zip(work, old)

    with pytest.raises(SystemExit) as e:
        upgrade(old, _build_new(tmp_path, manifest_version="1.0"),
                tmp_path / "never.zip")
    assert "NEWER" in str(e.value)
    assert not (tmp_path / "never.zip").exists()   # nothing written on refusal


def test_refuses_to_overwrite_an_input(tmp_path):
    old, new = _build_old(tmp_path), _build_new(tmp_path)
    with pytest.raises(SystemExit):
        upgrade(old, new, old)
