"""The thin agent factory: profile registry, prompt overlay assembly, the
per-profile build, and KB extract — all over the one shared engine."""
import zipfile

import pytest

from librarian import boot
from scripts.build_memory import list_profiles, assemble_prompt, build_profile, build
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


def test_ast_unknown_target_is_rejected():
    from scripts.build_memory import _download_ast_wheels
    with pytest.raises(SystemExit):                 # no network needed — fails on validation
        _download_ast_wheels("totally-bogus-target")


def test_ast_presets_and_offline_pin():
    from scripts.build_memory import _AST_TARGETS, _AST_DEFAULT_TARGET, _AST_WHEEL_SPECS
    assert _AST_DEFAULT_TARGET in _AST_TARGETS       # the default is a real target
    # the offline-critical pin must be EXACTLY 0.13.0 (pack 1.x is unusable offline)
    assert "tree-sitter-language-pack==0.13.0" in _AST_WHEEL_SPECS
    assert any(s.startswith("pypdf") for s in _AST_WHEEL_SPECS)
