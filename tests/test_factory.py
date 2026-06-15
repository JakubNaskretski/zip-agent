"""The thin agent factory: profile registry, prompt overlay assembly, the
per-profile build, and KB extract — all over the one shared engine."""
import zipfile

import pytest

from librarian import boot
from scripts.build_memory import (
    list_profiles, assemble_prompt, build_profile, build, upgrade_profile)
from scripts.extract_kb import extract
from factories import jira_ku


def test_profiles_are_discovered_from_dir():
    profiles = list_profiles()
    assert "rfp" in profiles
    assert "project" in profiles
    assert "_base" not in profiles          # the shared base is not a profile


def test_project_prompt_is_the_base_contract():
    p = assemble_prompt("project")
    assert "{{PROFILE_" not in p                        # every marker resolved/removed
    assert "## 1. Session start — boot" in p            # base contract present
    assert "from librarian.bootstrap import boot" in p  # boot snippet survived
    assert "PROPOSE" not in p                            # project has no RFP operation


def test_rfp_prompt_adds_discover_over_the_same_base():
    p = assemble_prompt("rfp")
    assert "{{PROFILE_" not in p
    assert "from librarian.bootstrap import boot" in p   # SAME base contract
    assert "## 4.2 DISCOVER" in p                        # operations overlay landed
    assert "CLIENT REQUIRES" in p and "OUR POC SHOWS" in p   # source-label discipline
    assert "RFP-pursuit co-pilot" in p                   # intro overlay landed
    assert "rfp.read_workbook" in p                      # excel cell-reader wired in


def test_unknown_profile_is_rejected():
    with pytest.raises(SystemExit):
        build_profile("does-not-exist")


def test_build_profile_emits_clean_zip_and_prompt_beside_it(tmp_path):
    out_dir, memzip, prompt_path = build_profile("rfp", out_dir=tmp_path)
    assert memzip.exists() and prompt_path.exists()
    with zipfile.ZipFile(memzip) as zf:
        names = zf.namelist()
    assert any(n.startswith("librarian/") and n.endswith("librarian.py") for n in names)
    assert any(n.startswith("graphbuilder/") for n in names)   # vendored engine shipped
    assert "MASTER_PROMPT.md" not in names                     # prompt is BESIDE, not inside
    assert "## 4.2 DISCOVER" in prompt_path.read_text("utf-8")
    # the clean zip boots into a working session
    session = boot(memzip, work_dir=tmp_path / "deployed")
    session.begin("dev", "first ingest on the rfp agent").add_ku(jira_ku(1), body="x").commit()
    assert session.get("jira:PROJ-1") is not None


def test_upgrade_profile_carries_kb_and_regenerates_prompt(tmp_path):
    """One-shot upgrade of a deployed, KB-loaded zip: new engine + carried KB +
    regenerated prompt, in place, without losing the original. Exercises the
    hardest path — the KB zip IS <out_dir>/memory.zip."""
    out = tmp_path / "rfp"
    build_profile("rfp", out_dir=out)                      # initial deploy (clean)
    # operator ingests knowledge straight into the deployed zip
    s = boot(out / "memory.zip", work_dir=tmp_path / "w")
    s.begin("dev", "ingest a project issue into the deployed agent").add_ku(
        jira_ku(99), body="acme order export bug").commit()
    assert s.get("jira:PROJ-99") is not None
    # an older prompt sits beside it; pretend it's stale so we can see the regen
    (out / "MASTER_PROMPT.md").write_text("STALE PROMPT", "utf-8")

    out_dir, memzip, prompt_path, changed = upgrade_profile("rfp", out / "memory.zip", out_dir=out)

    # the upgrade ships a REBUILT search index (not left for first boot): the
    # initial deploy never ran rebuild_indexes, so an index in the upgraded zip
    # can only have come from the upgrade itself
    with zipfile.ZipFile(memzip) as zf:
        assert any(n.startswith("kb/indexes/") for n in zf.namelist()), \
            "upgrade must ship a rebuilt search index, not a stripped one"
    # KB carried onto the rebuilt engine, and the shipped index is live
    s2 = boot(memzip, work_dir=tmp_path / "w2")
    assert s2.get("jira:PROJ-99") is not None
    from librarian import retrieve
    con = retrieve.open_index(s2.librarian)
    assert "jira:PROJ-99" in {h["ku_id"] for h in retrieve.find_entity(con, "MeterPointService")}
    # the original KB zip is preserved as the backup (never clobbered)
    assert (out / "memory.prev.zip").exists()
    s_prev = boot(out / "memory.prev.zip", work_dir=tmp_path / "wp")
    assert s_prev.get("jira:PROJ-99") is not None
    # prompt regenerated to the real contract, drift reported
    assert changed is True
    text = prompt_path.read_text("utf-8")
    assert text != "STALE PROMPT" and "## 4.2 DISCOVER" in text


def test_upgrade_carries_forward_the_bundled_wheelhouse(tmp_path):
    """An upgrade must NOT strip the offline capability the old zip already had:
    a bundled wheelhouse is carried forward verbatim with no --pptx/--ast flag."""
    wh = tmp_path / "wh"
    wh.mkdir()
    (wh / "acme_dep-1.0-py3-none-any.whl").write_bytes(b"PK\x03\x04 fake wheel bytes")
    init = build(tmp_path / "init.zip", wheelhouse=str(wh), slim=False)
    with zipfile.ZipFile(init) as zf:
        assert "reference/wheelhouse/acme_dep-1.0-py3-none-any.whl" in zf.namelist()
    s = boot(init, work_dir=tmp_path / "w")
    s.begin("dev", "ingest one issue before upgrade").add_ku(jira_ku(7), body="x").commit()

    out = tmp_path / "rfp"
    _, memzip, _, _ = upgrade_profile("rfp", init, out_dir=out)   # NO wheel flags

    with zipfile.ZipFile(memzip) as zf:
        names = zf.namelist()
    assert "reference/wheelhouse/acme_dep-1.0-py3-none-any.whl" in names, \
        "the upgrade must carry the old zip's wheelhouse forward, not strip it"
    assert boot(memzip, work_dir=tmp_path / "w2").get("jira:PROJ-7") is not None  # KB too


def test_upgrade_profile_requires_a_known_profile(tmp_path):
    memzip = tmp_path / "memory.zip"
    boot(memzip, work_dir=tmp_path / "w")
    with pytest.raises(SystemExit):
        upgrade_profile("does-not-exist", memzip, out_dir=tmp_path / "o")


def test_extract_kb_roundtrips_and_never_touches_input(tmp_path):
    memzip = tmp_path / "memory.zip"
    s = boot(memzip, work_dir=tmp_path / "w")
    s.begin("dev", "ingest issue one").add_ku(jira_ku(1), body="a").commit()
    s.begin("dev", "ingest issue two").add_ku(jira_ku(2), body="b").commit()
    before = memzip.read_bytes()

    bundle = extract(memzip, out=tmp_path / "kb-bundle.zip")
    assert bundle.exists()
    with zipfile.ZipFile(bundle) as zf:
        names = zf.namelist()
    assert "manifest.json" in names
    assert any(n.startswith("kb/") for n in names)
    assert not any(n.startswith("librarian/") for n in names)   # state only, no engine
    assert memzip.read_bytes() == before                        # input untouched


def test_extract_refuses_code_only_zip(tmp_path):
    code_only = build(tmp_path / "code.zip")    # an engine build: no manifest/state
    with pytest.raises(SystemExit):
        extract(code_only, out=tmp_path / "out.zip")


def test_extract_kb_index_drop_and_with_indexes(tmp_path):
    from librarian import rebuild_indexes
    memzip = tmp_path / "memory.zip"
    s = boot(memzip, work_dir=tmp_path / "w")
    s.begin("dev", "ingest one issue for the index").add_ku(jira_ku(1), body="a").commit()
    rebuild_indexes(s.librarian, "dev", "build the search index for the test")
    s.checkpoint()                                   # pack the index into memory.zip
    import json as _json
    # default: index FILES dropped AND their manifest entries removed (no dangling ref)
    bundle = extract(memzip, out=tmp_path / "kb.zip")
    with zipfile.ZipFile(bundle) as zf:
        names = zf.namelist()
        manifest = _json.loads(zf.read("manifest.json"))
    assert not any(n.startswith("kb/indexes/") for n in names)
    res = manifest.get("resources", manifest.get("kus", []))
    assert not any(r.get("tier") == "indexes" for r in res)
    # --with-indexes keeps the files
    bundle2 = extract(memzip, out=tmp_path / "kb2.zip", with_indexes=True)
    with zipfile.ZipFile(bundle2) as zf:
        assert any(n.startswith("kb/indexes/") for n in zf.namelist())


def test_ast_unknown_target_is_rejected():
    from scripts.build_memory import _download_wheels, _AST_WHEEL_SPECS
    with pytest.raises(SystemExit):                 # no network needed — fails on validation
        _download_wheels(_AST_WHEEL_SPECS, "totally-bogus-target")


def test_ast_presets_and_offline_pin():
    from scripts.build_memory import _AST_TARGETS, _AST_DEFAULT_TARGET, _AST_WHEEL_SPECS
    assert _AST_DEFAULT_TARGET in _AST_TARGETS       # the default is a real target
    # the offline-critical pin must be EXACTLY 0.13.0 (pack 1.x is unusable offline)
    assert "tree-sitter-language-pack==0.13.0" in _AST_WHEEL_SPECS
    assert any(s.startswith("pypdf") for s in _AST_WHEEL_SPECS)
