"""pptx-draft — on-demand presentation drafting (vendored pptx-grid-skill).

A thin subprocess wrapper the agent imports only when a deck is wanted
(``from librarian.skills import pptx_draft``). It drives the vendored bundle's
``reader.py`` (theme / recipes / validate — PyYAML only) and ``render.py`` (the
.pptx — needs python-pptx), each run with ``cwd=<bundle>`` so the bundle's sibling
imports, default ``theme.yaml`` and ``assets/`` resolve.

NOT imported by ``librarian/__init__.py`` → zero always-loaded context. The agent
follows the contract in ``pptx/SKILL.md`` (read on demand) and the rfp prompt
overlay's hook — not this source. The image/placeholder model and the rebrand
notes live in ``vendor/README.md``.

Pin: ``_VENDORED_SHA`` + ``vendor/README.md`` (single source of truth)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from ..schema import KnowledgeUnit

# pin recorded in the built-in tool KU (and vendor/README.md)
_VENDORED_SHA = "cf6388b"   # pptx-skill-grid — recipe/grid deck skill (skill/ tree)
_VENDORED_AT = "2026-06-14"

TOOL_ID = "agent:tool/pptx-draft"
TOOL_PATH = "tools/pptx-draft/PROVENANCE.json"

# repo root in a dev checkout; the ZIP root at runtime (bootstrap.boot() puts the
# unpacked root on sys.path) — parents[0]=skills, [1]=librarian, [2]=root.
_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_BUNDLE = _ROOT / "pptx"                  # the assembled grid bundle inside memory.zip
_VENDOR_DIR = _ROOT / "vendor" / "pptx_draft"    # dev only — bundled into pptx/ at build

_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")

PLACEHOLDER = {"placeholder": True}   # the decorative-image value; see placeholder()


def placeholder(label: str | None = None):
    """A decorative image slot the USER finishes — a labeled grey box render draws
    and the human pastes the real image over. ``placeholder("POC login screen")``
    -> ``{"placeholder": True, "label": "POC login screen"}``."""
    return {"placeholder": True, "label": label} if label else dict(PLACEHOLDER)


def assemble_bundle(dest, *, vendor_dir=None) -> Path:
    """Assemble the self-contained grid bundle at *dest* — a verbatim copy of the
    vendored ``skill/`` tree. Pure stdlib; the single assembly path shared by
    ``build_memory.py`` (staging ``pptx/``), tests, and dev."""
    dest = Path(dest)
    src = Path(vendor_dir) if vendor_dir else _VENDOR_DIR
    if not (src / "reader.py").is_file():
        raise FileNotFoundError(f"vendored grid skill not found at {src}/reader.py")
    shutil.copytree(src, dest, dirs_exist_ok=True, ignore=_IGNORE)
    return dest


def runtime_bundle() -> Path | None:
    """The deployed bundle inside memory.zip (``<root>/pptx``), or ``None`` in a
    dev checkout where it has not been assembled."""
    return RUNTIME_BUNDLE if (RUNTIME_BUNDLE / "reader.py").is_file() else None


def _bundle_or_raise(bundle_dir) -> Path:
    if bundle_dir is not None:
        bd = Path(bundle_dir)
    else:
        bd = runtime_bundle()
        if bd is None:
            raise FileNotFoundError(
                "no deployed pptx bundle at <root>/pptx. In a dev checkout, "
                "assemble one first: pptx_draft.assemble_bundle('/tmp/pptx-bundle').")
    if not (bd / "reader.py").is_file():
        raise FileNotFoundError(f"no reader.py in bundle dir {bd}")
    return bd


def _exec(script, *args, bundle_dir=None):
    """Run a bundle script (reader.py / render.py) with cwd=<bundle> (so its
    sibling imports + default ./theme.yaml + assets/ resolve). Returns the
    CompletedProcess."""
    bd = _bundle_or_raise(bundle_dir)
    cmd = [sys.executable, str(bd / script), *(str(a) for a in args)]
    return subprocess.run(cmd, cwd=str(bd), capture_output=True, text=True)


def run(verb, *args, bundle_dir=None, check=True):
    """Invoke a read-only ``reader.py`` verb and return its parsed JSON stdout.

    The generic passthrough for any reader verb (theme / list-recipes /
    recipe-signature / validate-slide / validate-plan / grid-audit /
    measure-text / contrast-check / opener-template-status / …). Read verbs need
    only PyYAML. Raises RuntimeError on a non-zero exit unless ``check=False``.
    File-path args should be ABSOLUTE (cwd is the bundle, not the caller's dir).
    """
    res = _exec("reader.py", verb, *args, bundle_dir=bundle_dir)
    if check and res.returncode != 0:
        raise RuntimeError(
            f"reader.py {verb} failed (exit {res.returncode}):\n{res.stderr.strip()}")
    if not res.stdout.strip():
        return {}
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return {"_stdout": res.stdout, "_stderr": res.stderr, "_rc": res.returncode}


# --- read-only verbs (catalog browse + the validate gate) ------------------- #
def theme(*, bundle_dir=None):
    """The calibrated theme (palette by name + fonts + type scale)."""
    return run("theme", bundle_dir=bundle_dir)


def list_recipes(*, bundle_dir=None):
    """The 26 parametric layout recipes the agent picks from."""
    return run("list-recipes", bundle_dir=bundle_dir)


def list_components(*, bundle_dir=None):
    return run("list-components", bundle_dir=bundle_dir)


def recipe_signature(name, *, bundle_dir=None):
    """The content/params a recipe accepts (call before composing a slide)."""
    return run("recipe-signature", name, bundle_dir=bundle_dir)


def opener_template_status(*, bundle_dir=None):
    """Whether a pre-rendered branded opener template is active."""
    return run("opener-template-status", bundle_dir=bundle_dir)


def validate_slide(slide_path, *, bundle_dir=None):
    """Phase-3 per-slide gate: recipe resolution + grid_audit + measure_text +
    palette/chart sanity. Returns {ok, slide_id, errors, warnings}."""
    return run("validate-slide", Path(slide_path).resolve(), bundle_dir=bundle_dir)


def validate_plan(plan_path, *, bundle_dir=None):
    """Phase-4 deck gate: per-slide checks + deck_flow. Returns {ok, errors,
    warnings}. ok must be true before rendering."""
    return run("validate-plan", Path(plan_path).resolve(), bundle_dir=bundle_dir)


# --- compose (renders the .pptx; needs python-pptx) ------------------------- #
def render(plan_path, out_path, *, theme=None, assets=None, no_splice=False,
           no_template_opener=False, bundle_dir=None, lib=None) -> dict:
    """Render a validated plan to a ``.pptx`` via the bundle's ``render.py``
    (needs python-pptx — bundle it offline with ``build_memory.py --pptx``).

    Auto-splices image binaries from the bundle's ``assets/`` when present (raster
    via Pillow); with no assets every image is a labeled grey-box placeholder.
    ``--theme`` defaults to the bundled ``theme.yaml``. Returns ``{ok, out,
    stdout, stderr}``. When *lib* is given and the render succeeds, the built-in
    tool KU is registered once (idempotent provenance). Grid writes NO warnings
    sidecar — the finish-this-deck punch-list comes from the plan's placeholder
    labels + ``validate_plan`` output.
    """
    args = [Path(plan_path).resolve(), Path(out_path).resolve()]
    if theme:
        args += ["--theme", Path(theme).resolve()]
    if assets:
        args += ["--assets", Path(assets).resolve()]
    if no_splice:
        args.append("--no-splice")
    if no_template_opener:
        args.append("--no-template-opener")
    res = _exec("render.py", *args, bundle_dir=bundle_dir)
    result = {
        "ok": res.returncode == 0,
        "out": str(Path(out_path).resolve()) if res.returncode == 0 else None,
        "stdout": res.stdout.strip(),
        "stderr": res.stderr.strip(),
    }
    if lib is not None and result["ok"]:
        ensure_registered(lib)
    return result


# --- built-in tool KU (records the vendored skill version + pin) ------------ #
def _tool_provenance() -> dict:
    return {
        "package": "pptx-grid-skill",
        "vendored_sha": _VENDORED_SHA,
        "vendored_at": _VENDORED_AT,
        "source_repo": "pptx-skill-grid (public)",
        "direction": "authoring — RFP deck DRAFT via recipes on a 12x12 grid",
        "doc": "vendor/README.md",
    }


def _tool_ku():
    prov = _tool_provenance()
    return KnowledgeUnit(
        id=TOOL_ID, kind="tool", tier="built-in", source="agent",
        path=TOOL_PATH,
        title="pptx-draft — vendored pptx-grid-skill recipe/grid deck composer",
        entities=["pptx-draft", "pptx", "pptx-grid-skill"], confidence="VERIFIED",
        provenance=prov,
    ), json.dumps(prov, ensure_ascii=False, indent=2)


def ensure_registered(lib, author="pptx-draft",
                       rationale="register pptx-draft skill provenance") -> bool:
    """Record the built-in tool KU once (idempotent) — provenance of the vendored
    grid skill. Returns True if newly registered. A no-op once present (I9)."""
    if lib.get(TOOL_ID) is not None:
        return False
    txn = lib.begin(author, rationale)
    ku, body = _tool_ku()
    txn.add_ku(ku, body=body)
    txn.commit()
    return True
