"""The thin agent factory: profile registry, prompt overlay assembly, the
per-profile build, and KB extract — all over the one shared engine."""
import zipfile

import pytest

from librarian import boot
from runtime import boot as lean_boot
from runtime.ingest import digest_to_tree
from runtime.storage import Workspace
from scripts.build_memory import (
    list_profiles, assemble_prompt, build_profile, build, upgrade_profile)
from scripts.extract_kb import extract
from factories import jira_ku
from tests.test_digest_graphbuilder import make_force_app


def test_profiles_are_discovered_from_dir():
    profiles = list_profiles()
    assert "rfp" in profiles
    assert "project" in profiles
    assert "_base" not in profiles          # the shared base is not a profile


def test_project_prompt_is_the_base_contract():
    p = assemble_prompt("project")
    assert "{{PROFILE_" not in p                        # every marker resolved/removed
    assert "## 1. Session start — boot" in p            # base contract present
    assert "from runtime import boot" in p              # lean boot snippet survived
    assert "DISCOVER" not in p                           # project has no RFP operation


def test_rfp_prompt_adds_discover_over_the_same_base():
    p = assemble_prompt("rfp")
    assert "{{PROFILE_" not in p
    assert "from runtime import boot" in p               # SAME lean base contract
    assert "## 4.2 DISCOVER" in p                        # operations overlay landed
    assert "CLIENT REQUIRES" in p and "OUR POC SHOWS" in p   # source-label discipline
    assert "RFP-pursuit co-pilot" in p                   # intro overlay landed
    assert "docs.read_workbook" in p                     # excel cell-reader wired in


def test_unknown_profile_is_rejected():
    with pytest.raises(SystemExit):
        build_profile("does-not-exist")


def test_build_profile_emits_clean_zip_and_prompt_beside_it(tmp_path):
    out_dir, memzip, prompt_path = build_profile("rfp", out_dir=tmp_path)
    assert memzip.exists() and prompt_path.exists()
    with zipfile.ZipFile(memzip) as zf:
        names = zf.namelist()
    assert any(n.startswith("runtime/") for n in names)            # the lean engine ships
    assert "agent_manifest.json" in names and "index/L0.md" in names
    assert any(n.startswith("graphbuilder/") for n in names)       # parser engine for ingest
    assert "librarian/librarian.py" not in names                   # held engine NOT shipped
    assert "MASTER_PROMPT.md" not in names                         # prompt is BESIDE, not inside
    assert "## 4.2 DISCOVER" in prompt_path.read_text("utf-8")
    # the clean zip boots via the lean runtime into an empty (no-KB) session
    session = lean_boot(str(memzip), str(tmp_path / "deployed"))
    assert session.sources() == [] and session.l0          # L0 map loaded, no KB yet


def test_upgrade_profile_carries_kb_and_regenerates_prompt(tmp_path):
    """Upgrade = carry a deployed agent's KB onto the current lean engine +
    regenerate the prompt, backing up the previous zip (never clobbered)."""
    # a deployed lean agent with real KB (a Salesforce digest)
    make_force_app(tmp_path)
    ws = Workspace(None, str(tmp_path / "work"))
    digest_to_tree(ws, "salesforce", str(tmp_path / "force-app"))
    deployed = ws.export(str(tmp_path / "deployed.zip"))

    out = tmp_path / "rfp"
    out.mkdir()
    (out / "memory.zip").write_bytes(b"PK\x03\x04 prior zip")       # something to back up
    (out / "MASTER_PROMPT.md").write_text("STALE PROMPT", "utf-8")

    out_dir, memzip, prompt_path, changed = upgrade_profile("rfp", str(deployed), out_dir=out)

    # KB carried onto the rebuilt lean engine
    s2 = lean_boot(str(memzip), str(tmp_path / "w2"))
    assert "salesforce" in s2.sources()
    assert any(n["id"] == "object/MeterPoint__c" for n in s2.shard("salesforce")["nodes"])
    # the previous zip is preserved as the backup (never clobbered)
    assert (out / "memory.prev.zip").exists()
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

    out = tmp_path / "rfp"
    _, memzip, _, _ = upgrade_profile("rfp", init, out_dir=out)   # NO wheel flags

    with zipfile.ZipFile(memzip) as zf:
        assert "reference/wheelhouse/acme_dep-1.0-py3-none-any.whl" in zf.namelist(), \
            "the upgrade must carry the old zip's wheelhouse forward, not strip it"


def test_upgrade_profile_requires_a_known_profile(tmp_path):
    fake = tmp_path / "any.zip"
    fake.write_bytes(b"PK\x03\x04")
    with pytest.raises(SystemExit):                          # profile checked before the zip
        upgrade_profile("does-not-exist", fake, out_dir=tmp_path / "o")


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


def test_extract_kb_has_no_persisted_index_tier(tmp_path):
    """On this branch the search index is built in memory at open time — there is
    NO persisted index KU. rebuild_indexes is a no-op, so a checkpointed zip ships
    no kb/indexes/ files and no indexes-tier manifest entry, and extract carries
    the real KB cleanly. (The extract --with-indexes path remains for legacy zips
    that may still carry a kb/indexes/ blob; with none present it simply has
    nothing extra to keep.)"""
    from librarian import rebuild_indexes
    memzip = tmp_path / "memory.zip"
    s = boot(memzip, work_dir=tmp_path / "w")
    s.begin("dev", "ingest one issue").add_ku(jira_ku(1), body="a").commit()
    rebuild_indexes(s.librarian, "dev", "no-op rebuild (index is in memory now)")
    s.checkpoint()
    import json as _json
    # no persisted index: no kb/indexes/ files anywhere in the zip
    with zipfile.ZipFile(memzip) as zf:
        assert not any(n.startswith("kb/indexes/") for n in zf.namelist())
    # extract carries the real KB and never produces an indexes tier
    bundle = extract(memzip, out=tmp_path / "kb.zip")
    with zipfile.ZipFile(bundle) as zf:
        names = zf.namelist()
        manifest = _json.loads(zf.read("manifest.json"))
    assert "kb/raw/jira/PROJ-1.json" in names
    assert not any(n.startswith("kb/indexes/") for n in names)
    res = manifest.get("resources", manifest.get("kus", []))
    assert not any(r.get("tier") == "indexes" for r in res)
    # --with-indexes is still accepted and round-trips the same KB (nothing to keep)
    bundle2 = extract(memzip, out=tmp_path / "kb2.zip", with_indexes=True)
    with zipfile.ZipFile(bundle2) as zf:
        assert not any(n.startswith("kb/indexes/") for n in zf.namelist())


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
