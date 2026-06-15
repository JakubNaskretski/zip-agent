"""The on-demand pptx-draft skill (librarian/skills/pptx_draft.py), now backed by
the vendored pptx-grid-skill (recipes + 12x12 grid).

Covers: zero always-loaded cost; the bundle assembles self-contained; the vendored
reader.py read/validate verbs drive through the wrapper; an in-sandbox render of
the bundled example plan; the built-in tool KU + pin; the build ships pptx/ at the
ZIP root; the boot skip-guard covers the pptx wheel stack; and the anonymization
scrub held. Read/render tests shell the vendored reader.py/render.py, which need
PyYAML (read/validate) and python-pptx (render) — the dev extra installs both;
tests importorskip when run without it.
"""
import json
import zipfile
from pathlib import Path

import pytest

from librarian import boot
from librarian.skills import pptx_draft


def _bundle(tmp_path):
    return pptx_draft.assemble_bundle(tmp_path / "pptx")


def _example_plan(bundle):
    return bundle / "examples" / "example_plan.json"


def test_skill_is_not_eagerly_imported():
    """The crux of the on-demand requirement: importing the engine must NOT pull
    the skill in (zero always-loaded context). Checked in a FRESH interpreter —
    this test process has already imported the skill itself."""
    import subprocess
    import sys

    code = ("import sys, librarian; "
            "assert 'librarian.skills.pptx_draft' not in sys.modules, 'skill eagerly imported'; "
            "print('ok')")
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "ok"


def test_placeholder_helper():
    assert pptx_draft.placeholder() == {"placeholder": True}
    assert pptx_draft.placeholder("POC login screen") == {
        "placeholder": True, "label": "POC login screen"}


def test_assemble_bundle_is_self_contained(tmp_path):
    b = _bundle(tmp_path)
    assert (b / "reader.py").is_file()          # read/validate verbs
    assert (b / "render.py").is_file()          # composes the .pptx
    assert (b / "SKILL.md").is_file()           # the on-demand agent contract
    assert (b / "theme.yaml").is_file()         # calibrated theme (render --theme default)
    assert (b / "recipes" / "__init__.py").is_file()
    assert (b / "schemas" / "plan.schema.json").is_file()
    assert _example_plan(b).is_file()           # render-test fixture
    assert pptx_draft.runtime_bundle() is None   # no deployed bundle in a dev checkout


def test_vendored_tree_is_anonymized(tmp_path):
    """Anonymization: the one real company name in grid's example data was scrubbed
    to a fictional one, and the dropped brand-named example decks are absent."""
    b = _bundle(tmp_path)
    recipes_src = (b / "recipes" / "__init__.py").read_text(encoding="utf-8")
    assert "Ex-fintech FP&A." in recipes_src        # the fictional replacement landed
    assert not (b / "examples" / "example_branded.json").exists()
    assert not (b / "examples" / "example_showcase.json").exists()


def test_read_verbs_over_vendored_reader(tmp_path):
    pytest.importorskip("yaml")                  # reader.py read verbs need PyYAML
    b = _bundle(tmp_path)
    recipes = pptx_draft.list_recipes(bundle_dir=b)
    assert isinstance(recipes, list) and len(recipes) >= 20   # the 26 grid recipes
    th = pptx_draft.theme(bundle_dir=b)
    assert isinstance(th, dict) and "palette" in th and "fonts" in th


def test_validate_plan_and_slide(tmp_path):
    pytest.importorskip("yaml")
    b = _bundle(tmp_path)
    vp = pptx_draft.validate_plan(_example_plan(b), bundle_dir=b)
    assert vp.get("ok") is True, vp
    # a single slide pulled from the known-good plan validates too
    plan = json.loads(_example_plan(b).read_text(encoding="utf-8"))
    slide0 = (plan["slides"] if isinstance(plan, dict) else plan)[0]
    sp = tmp_path / "slide0.json"
    sp.write_text(json.dumps(slide0), encoding="utf-8")
    vs = pptx_draft.validate_slide(sp, bundle_dir=b)
    assert "ok" in vs


def test_render_produces_a_deck_in_sandbox(tmp_path):
    pytest.importorskip("yaml")
    pytest.importorskip("pptx")                  # render.py needs python-pptx
    b = _bundle(tmp_path)
    out = tmp_path / "deck.pptx"
    res = pptx_draft.render(_example_plan(b), out, bundle_dir=b)
    assert res["ok"], res
    assert out.is_file() and out.stat().st_size > 0
    with zipfile.ZipFile(out) as z:
        slides = [n for n in z.namelist()
                  if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
    assert len(slides) >= 1                       # a real, multi-slide .pptx


def test_tool_ku_registers_with_the_pin_idempotently(tmp_path):
    lib = boot(tmp_path / "memory.zip", work_dir=tmp_path / "w").librarian
    assert lib.get(pptx_draft.TOOL_ID) is None
    assert pptx_draft.ensure_registered(lib) is True
    tool = lib.get(pptx_draft.TOOL_ID)
    assert tool is not None
    assert tool.tier == "built-in" and tool.kind == "tool" and tool.source == "agent"
    assert tool.provenance.get("vendored_sha") == pptx_draft._VENDORED_SHA == "cf6388b"
    assert pptx_draft.ensure_registered(lib) is False           # no-op once present


def test_boot_skip_guard_covers_the_pptx_stack(tmp_path, monkeypatch):
    """The boot wheelhouse skip-guard derives its probe from the bundled wheels,
    so a --pptx deploy whose render stack already imports does NOT re-run pip."""
    import subprocess
    import sys
    import types

    from librarian.bootstrap import _install_wheelhouse
    wh = tmp_path / "reference" / "wheelhouse"
    wh.mkdir(parents=True)
    (wh / "python_pptx-1.0.2-py3-none-any.whl").write_bytes(b"x")
    (wh / "PyYAML-6.0.3-cp312-cp312-manylinux2014_x86_64.whl").write_bytes(b"x")
    (wh / "pillow-10.4.0-cp312-cp312-manylinux2014_x86_64.whl").write_bytes(b"x")
    for name, mod in (("pptx", "pptx"), ("yaml", "yaml"), ("PIL", "PIL")):
        monkeypatch.setitem(sys.modules, mod, types.ModuleType(name))

    def _no_pip(*a, **kw):
        raise AssertionError("pip must not run when the bundled stack already imports")
    monkeypatch.setattr(subprocess, "run", _no_pip)
    assert _install_wheelhouse(tmp_path) == {"installed": True, "skipped": "already importable"}


def test_build_ships_the_pptx_bundle_at_zip_root(tmp_path):
    """A profile build assembles and ships the grid bundle at the ZIP root, beside
    graphbuilder/, with reader.py + render.py + theme.yaml + recipes present."""
    from scripts.build_memory import build_profile
    _out, memzip, _prompt = build_profile("rfp", out_dir=tmp_path)
    with zipfile.ZipFile(memzip) as zf:
        names = zf.namelist()
    assert any(n == "pptx/reader.py" for n in names)
    assert any(n == "pptx/render.py" for n in names)
    assert any(n == "pptx/SKILL.md" for n in names)
    assert any(n == "pptx/theme.yaml" for n in names)
    assert any(n.startswith("pptx/recipes/") for n in names)
