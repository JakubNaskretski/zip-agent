"""pptx-draft — on-demand presentation drafting (vendored pptx-skill v5).

ON-DEMAND: this module is NOT imported by ``librarian/__init__.py``, so it costs
zero always-loaded context. The agent reaches it only when a deck is wanted, via
``from librarian.skills import pptx_draft``. Discoverability is a single routing
line in the rfp profile overlay — never the full instructions.

Direction: **knowledge -> deck DRAFT** — the inverse of
``librarian/digest/office.py`` (which is deck -> knowledge). The agent grounds
slide content in its KUs / labelled findings, drafts a v5 plan that leaves
PLACEHOLDER slots for what the user must finish, then renders a ``.pptx``
*in-sandbox* via the vendored consumer reader.

The vendored consumer (``vendor/pptx_draft/reader.py`` + ``SKILL.md``) finds its
bundle via its own ``__file__`` location, so reader.py and the template DATA
(``index.json`` / ``themes/`` / ``skeletons/`` / ``assets/``) must live in ONE
directory. :func:`assemble_bundle` builds that directory; ``build_memory.py``
stages it at the ZIP root as ``pptx/`` and this module drives it as a subprocess
(the same way the pptx-skill app does).

Rendering needs python-pptx — NOT a runtime dependency of the engine. Bundle it
offline for the sandbox with ``scripts/build_memory.py --pptx`` (mirrors
``--ast``); the read-only verbs need only PyYAML, also in that wheel set.

Vendoring pin: see ``_VENDORED_SHA`` below and ``vendor/README.md`` — the single
sources of truth (prose carries no copy of the SHA; a stale duplicate misleads).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from ..schema import KnowledgeUnit

# pin recorded in the built-in tool KU (and vendor/README.md)
_VENDORED_SHA = "536c87d"   # pptx-skill chore/v5-tidy — v5 consumer (reader.py + SKILL.md)
_VENDORED_AT = "2026-06-14"

TOOL_ID = "agent:tool/pptx-draft"
TOOL_PATH = "tools/pptx-draft/PROVENANCE.json"

# repo root in a dev checkout; the ZIP root at runtime (bootstrap.boot() puts the
# unpacked root on sys.path) — parents[0]=skills, [1]=librarian, [2]=root.
_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_BUNDLE = _ROOT / "pptx"                          # assembled bundle inside memory.zip
_VENDOR_DIR = _ROOT / "vendor" / "pptx_draft"            # dev only — not shipped in the zip
_TEMPLATES_DIR = _ROOT / "reference" / "pptx-templates"  # dev only — bundled into pptx/ at build
DEFAULT_TEMPLATE = "acme"

# what assemble_bundle pulls from each source dir
_READER_FILES = ("reader.py", "SKILL.md", "requirements.txt")
_TEMPLATE_PARTS = ("index.json", "themes", "skeletons", "assets", "tag_vocab.yaml", "brand.md")
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")

# the canonical "user fills this in" image value (see SKILL.md "Picking images")
PLACEHOLDER = "placeholder"


def placeholder(label: str | None = None):
    """A draft image slot the USER finishes: a labeled dashed grey box that
    compose renders + flags in the warnings sidecar. ``placeholder("Logo here")``
    -> ``{"placeholder": True, "label": "Logo here"}``; no label -> the literal
    ``"placeholder"``."""
    return {"placeholder": True, "label": label} if label else PLACEHOLDER


def templates_root() -> Path:
    """Directory of available template bundles in the dev repo."""
    return _TEMPLATES_DIR


def list_templates() -> list:
    """Template bundles available in the dev repo (``reference/pptx-templates/<name>``)."""
    if not _TEMPLATES_DIR.is_dir():
        return []
    return sorted(p.name for p in _TEMPLATES_DIR.iterdir() if p.is_dir())


def assemble_bundle(dest, *, template=DEFAULT_TEMPLATE, reader_dir=None,
                    template_dir=None) -> Path:
    """Assemble a self-contained v5 bundle at *dest*: the vendored consumer
    (reader.py + SKILL.md + requirements.txt) beside a template's data. reader.py
    locates its bundle from its own path, so everything lives in one directory.

    Pure stdlib; the single assembly path shared by ``build_memory.py`` (staging
    ``pptx/`` in the zip), tests, and dev. Returns *dest*.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    reader_dir = Path(reader_dir) if reader_dir else _VENDOR_DIR
    template_dir = Path(template_dir) if template_dir else (_TEMPLATES_DIR / template)
    if not (reader_dir / "reader.py").is_file():
        raise FileNotFoundError(f"vendored reader not found at {reader_dir}/reader.py")
    if not template_dir.is_dir():
        raise FileNotFoundError(f"template not found at {template_dir}")
    for name in _READER_FILES:
        src = reader_dir / name
        if src.is_file():
            shutil.copy2(src, dest / name)
    for part in _TEMPLATE_PARTS:
        src = template_dir / part
        if src.is_dir():
            shutil.copytree(src, dest / part, dirs_exist_ok=True, ignore=_IGNORE)
        elif src.is_file():
            shutil.copy2(src, dest / part)
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
                "assemble one first: "
                "pptx_draft.assemble_bundle('/tmp/pptx-bundle').")
    if not (bd / "reader.py").is_file():
        raise FileNotFoundError(f"no reader.py in bundle dir {bd}")
    return bd


def _run(verb, *args, bundle_dir=None):
    bd = _bundle_or_raise(bundle_dir)
    cmd = [sys.executable, str(bd / "reader.py"), verb, *(str(a) for a in args)]
    return subprocess.run(cmd, capture_output=True, text=True)


def run(verb, *args, bundle_dir=None, check=True):
    """Invoke a read-only reader verb and return its parsed JSON stdout.

    The same subprocess contract the pptx-skill app uses. For ``compose-v5`` use
    :func:`compose` (it also returns the warnings-sidecar path). Raises
    RuntimeError on a non-zero exit unless ``check=False``.
    """
    res = _run(verb, *args, bundle_dir=bundle_dir)
    if check and res.returncode != 0:
        raise RuntimeError(
            f"reader.py {verb} failed (exit {res.returncode}):\n{res.stderr.strip()}")
    if not res.stdout.strip():
        return {}
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return {"_stdout": res.stdout, "_stderr": res.stderr, "_rc": res.returncode}


# --- read-only verbs (catalog browsing + plan checking) --------------------- #
def list_themes(*, bundle_dir=None):
    return run("list-themes", bundle_dir=bundle_dir)


def list_skeletons(*, category=None, has_slot=None, bundle_dir=None):
    args = []
    if category:
        args += ["--category", category]
    if has_slot:
        args += ["--has-slot", has_slot]
    return run("list-skeletons", *args, bundle_dir=bundle_dir)


def get_skeleton(skeleton_id, *, bundle_dir=None):
    return run("get-skeleton", skeleton_id, bundle_dir=bundle_dir)


def match_skeletons(content, *, category=None, bundle_dir=None):
    """Rank skeletons against *content* (a dict, JSON-encoded for the CLI, or a
    JSON string). Returns candidates with fit_score / slot_mapping / issues."""
    blob = content if isinstance(content, str) else json.dumps(content)
    args = ["--content", blob]
    if category:
        args += ["--category", category]
    return run("match-skeletons", *args, bundle_dir=bundle_dir)


def find_asset(*, kind, tags=None, limit=None, bundle_dir=None):
    """Offline note: this deployment has NO asset library and NO authoring host.
    The starter template ships zero images, so this returns ``matches: []`` for
    any photo/logo/diagram and its ``suggestion`` text mentions
    ``POST /api/asset/add`` — IGNORE that, there is no host. Every image slot is
    a :func:`placeholder` the user pastes into by hand; you normally need not
    call this at all in this profile."""
    args = ["--kind", kind]
    for t in (tags or []):
        args += ["--tags", t]
    if limit:
        args += ["--limit", str(limit)]
    return run("find-asset", *args, bundle_dir=bundle_dir)


def validate_plan(plan_path, *, bundle_dir=None):
    """Pre-flight a plan.json -> {ok, errors, warnings}. Read-only (no render)."""
    return run("validate-plan", plan_path, bundle_dir=bundle_dir)


# --- compose (in-sandbox render; needs python-pptx, see --pptx wheelhouse) --- #
def compose(plan_path, out_path, *, theme, force=False, bundle_dir=None, lib=None) -> dict:
    """Render a validated plan to a ``.pptx`` IN-SANDBOX.

    Needs python-pptx (bundle it offline with ``build_memory.py --pptx``).
    compose-v5 re-runs the validator first and aborts on hard errors unless
    ``force`` (which carries them into the warnings sidecar). Returns
    ``{ok, out, warnings, stdout, stderr}``. When *lib* is given and the render
    succeeds, the built-in tool KU is registered once (idempotent provenance).
    """
    args = [plan_path, out_path, "--theme", theme]
    if force:
        args.append("--force")
    res = _run("compose-v5", *args, bundle_dir=bundle_dir)   # same subprocess contract as the read verbs
    warnings_path = Path(f"{out_path}.warnings.json")
    result = {
        "ok": res.returncode == 0,
        "out": str(out_path) if res.returncode == 0 else None,
        "warnings": str(warnings_path) if warnings_path.exists() else None,
        "stdout": res.stdout.strip(),
        "stderr": res.stderr.strip(),
    }
    if lib is not None and result["ok"]:
        ensure_registered(lib)
    return result


# --- built-in tool KU (records the vendored consumer version + pin) --------- #
def _tool_provenance() -> dict:
    return {
        "package": "pptx-skill (v5 consumer)",
        "vendored_sha": _VENDORED_SHA,
        "vendored_at": _VENDORED_AT,
        "source_repo": "pptx-skill (public)",
        "direction": "authoring — knowledge -> deck DRAFT (inverse of the office digest)",
        "doc": "vendor/README.md",
    }


def _tool_ku():
    prov = _tool_provenance()
    return KnowledgeUnit(
        id=TOOL_ID, kind="tool", tier="built-in", source="agent",
        path=TOOL_PATH,
        title="pptx-draft — vendored pptx-skill v5 presentation-authoring consumer",
        entities=["pptx-draft", "pptx"], confidence="VERIFIED",
        provenance=prov,
    ), json.dumps(prov, ensure_ascii=False, indent=2)


def ensure_registered(lib, author="pptx-draft",
                       rationale="register pptx-draft skill provenance") -> bool:
    """Record the built-in tool KU once (idempotent) — provenance of the vendored
    authoring consumer. Returns True if it was newly registered. Safe to call
    repeatedly; a no-op once present (I9)."""
    if lib.get(TOOL_ID) is not None:
        return False
    txn = lib.begin(author, rationale)
    ku, body = _tool_ku()
    txn.add_ku(ku, body=body)
    txn.commit()
    return True
