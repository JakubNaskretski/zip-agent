"""The on-demand pptx-draft skill (librarian/skills/pptx_draft.py).

Covers the four things that matter for this integration:
  * it adds ZERO always-loaded cost (the engine never imports it eagerly);
  * assemble_bundle produces a self-contained, renderable bundle;
  * the vendored reader.py drives through it (read verbs + an in-sandbox render);
  * the built-in tool KU records the vendored pin, idempotently.

The read/render tests shell out to the vendored reader.py, which needs PyYAML
(read verbs) and python-pptx (compose-v5) — the dev extra installs both; the
tests importorskip if run without it. They also assert the sample master.pptx
carries no real personal name (anonymization).
"""
import json
import re
import zipfile
from pathlib import Path

import pytest

from librarian import boot
from librarian.skills import pptx_draft


REPO_TEMPLATE = pptx_draft.templates_root() / pptx_draft.DEFAULT_TEMPLATE


def _bundle(tmp_path):
    return pptx_draft.assemble_bundle(tmp_path / "pptx")


def test_skill_is_not_eagerly_imported():
    """The crux of the on-demand requirement: importing the engine must NOT pull
    the skill in, so it adds zero always-loaded context. Checked in a FRESH
    interpreter — this test process has already imported the skill itself, so a
    sys.modules check here must run in a clean subprocess."""
    import subprocess
    import sys

    code = ("import sys, librarian; "
            "assert 'librarian.skills.pptx_draft' not in sys.modules, 'skill eagerly imported'; "
            "print('ok')")
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "ok"


def test_default_template_is_discoverable():
    assert pptx_draft.DEFAULT_TEMPLATE in pptx_draft.list_templates()
    assert REPO_TEMPLATE.is_dir()


def test_placeholder_helper():
    assert pptx_draft.placeholder() == "placeholder"
    assert pptx_draft.placeholder("Customer logo here") == {
        "placeholder": True, "label": "Customer logo here"}


def test_assemble_bundle_is_self_contained(tmp_path):
    bundle = _bundle(tmp_path)
    assert (bundle / "reader.py").is_file()          # vendored consumer
    assert (bundle / "SKILL.md").is_file()           # vendored contract
    assert (bundle / "index.json").is_file()         # template catalog
    assert (bundle / "themes" / "Acme" / "master.pptx").is_file()      # host master for compose
    assert (bundle / "skeletons" / "Acme_01" / "skeleton.yaml").is_file()
    assert pptx_draft.runtime_bundle() is None        # no deployed bundle in a dev checkout


def test_sample_master_carries_no_personal_name():
    """Anonymization: the python-pptx default master ships a real author name in
    docProps; the committed sample must have it scrubbed to empty."""
    master = REPO_TEMPLATE / "themes" / "Acme" / "master.pptx"
    with zipfile.ZipFile(master) as z:
        core = z.read("docProps/core.xml").decode("utf-8")
    m = re.search(r"<cp:lastModifiedBy>([^<]*)</cp:lastModifiedBy>", core)
    assert (m is None) or (m.group(1).strip() == ""), "personal name left in master.pptx"


def test_missing_bundle_raises_clearly(tmp_path):
    with pytest.raises(FileNotFoundError):
        pptx_draft.validate_plan(str(tmp_path / "plan.json"), bundle_dir=tmp_path / "nope")


def test_read_verbs_over_vendored_reader(tmp_path):
    pytest.importorskip("yaml")                      # reader.py read verbs import PyYAML
    bundle = _bundle(tmp_path)
    assert "Acme" in json.dumps(pptx_draft.list_themes(bundle_dir=bundle))
    skels = json.dumps(pptx_draft.list_skeletons(bundle_dir=bundle))
    assert "Acme_01" in skels and "Acme_05" in skels
    # match-skeletons runs and returns structured JSON (shape varies; don't over-fit)
    m = pptx_draft.match_skeletons(
        {"title": "Agenda", "bullets": ["Goals", "Approach", "Next steps"]},
        bundle_dir=bundle)
    assert isinstance(m, (dict, list))


def test_validate_plan_accepts_a_well_formed_plan(tmp_path):
    pytest.importorskip("yaml")
    bundle = _bundle(tmp_path)
    plan = [{"skeleton_id": "Acme_02",
             "slots": {"title": "Agenda", "body": ["Goals", "Approach", "Next steps"]}}]
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    res = pptx_draft.validate_plan(str(plan_path), bundle_dir=bundle)
    assert res.get("ok") is True, res


def test_compose_renders_a_deck_in_sandbox(tmp_path):
    pytest.importorskip("yaml")
    pytest.importorskip("pptx")                      # compose-v5 needs python-pptx
    bundle = _bundle(tmp_path)
    plan = [
        {"skeleton_id": "Acme_01",
         "slots": {"title": "Acme Proposal", "subtitle": "Draft for review"}},
        {"skeleton_id": "Acme_02",
         "slots": {"title": "Agenda", "body": ["Goals", "Approach", "Next steps"]}},
    ]
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    out = tmp_path / "draft.pptx"
    res = pptx_draft.compose(str(plan_path), str(out), theme="Acme", bundle_dir=bundle)
    assert res["ok"], res
    assert out.is_file() and out.stat().st_size > 0
    with zipfile.ZipFile(out) as z:                  # it is a real .pptx package
        assert "ppt/presentation.xml" in z.namelist()


def test_image_slot_renders_an_editable_placeholder_box(tmp_path):
    """Acme_07 exposes a required image slot; a placeholder must compose into an
    EDITABLE grey rectangle (an autoshape, not an embedded picture) carrying the
    label so a human can paste the real image over it. This is the headline
    offline/no-vision flow — and the render path is otherwise uncovered in CI."""
    pytest.importorskip("yaml")
    pytest.importorskip("pptx")
    bundle = _bundle(tmp_path)
    plan = [{
        "skeleton_id": "Acme_07",
        "slots": {
            "title": "Solution Overview",
            "body": ["Native Salesforce", "Fits the process", "Proven on the POC"],
            "hero": {"placeholder": True, "label": "POC screenshot - login flow"},
        },
    }]
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    out = tmp_path / "hero.pptx"
    res = pptx_draft.compose(str(plan_path), str(out), theme="Acme", bundle_dir=bundle)
    assert res["ok"], res
    assert res["warnings"], "a placeholder image slot must produce a warnings sidecar"
    assert "image_placeholder" in Path(res["warnings"]).read_text(encoding="utf-8")
    with zipfile.ZipFile(out) as z:
        slide_xml = next(z.read(n).decode("utf-8") for n in z.namelist()
                         if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
    assert "POC screenshot - login flow" in slide_xml      # label is visible to the human
    assert "<p:sp" in slide_xml and "<p:pic" not in slide_xml   # editable shape, not a locked picture


def test_tool_ku_registers_with_the_pin_idempotently(tmp_path):
    lib = boot(tmp_path / "memory.zip", work_dir=tmp_path / "w").librarian
    assert lib.get(pptx_draft.TOOL_ID) is None
    assert pptx_draft.ensure_registered(lib) is True
    tool = lib.get(pptx_draft.TOOL_ID)
    assert tool is not None
    assert tool.tier == "built-in" and tool.kind == "tool" and tool.source == "agent"
    assert tool.provenance.get("vendored_sha") == pptx_draft._VENDORED_SHA
    assert pptx_draft.ensure_registered(lib) is False           # no-op once present


def test_boot_skip_guard_covers_the_pptx_stack(tmp_path, monkeypatch):
    """The boot wheelhouse skip-guard derives its probe from the bundled wheels,
    so a --pptx deploy whose render stack already imports does NOT re-run pip on
    every reboot (the AST-only guard used to miss the pptx/lxml/Pillow wheels)."""
    import subprocess
    import sys
    import types

    from librarian.bootstrap import _install_wheelhouse
    wh = tmp_path / "reference" / "wheelhouse"
    wh.mkdir(parents=True)
    (wh / "python_pptx-1.0.2-py3-none-any.whl").write_bytes(b"x")
    (wh / "PyYAML-6.0.3-cp312-cp312-manylinux2014_x86_64.whl").write_bytes(b"x")
    for name in ("pptx", "yaml"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

    def _no_pip(*a, **kw):
        raise AssertionError("pip must not run when the bundled stack already imports")
    monkeypatch.setattr(subprocess, "run", _no_pip)
    assert _install_wheelhouse(tmp_path) == {"installed": True, "skipped": "already importable"}


def test_build_ships_the_pptx_bundle_at_zip_root(tmp_path):
    """A profile build assembles and ships the pptx/ bundle at the ZIP root,
    beside graphbuilder/, with the host master present for in-sandbox compose."""
    from scripts.build_memory import build_profile
    _out, memzip, _prompt = build_profile("rfp", out_dir=tmp_path)
    with zipfile.ZipFile(memzip) as zf:
        names = zf.namelist()
    assert any(n == "pptx/reader.py" for n in names)
    assert any(n == "pptx/SKILL.md" for n in names)
    assert any(n.startswith("pptx/themes/Acme/") and n.endswith("master.pptx") for n in names)
    assert any(n.startswith("pptx/skeletons/Acme_01/") for n in names)
