"""Assemble a deployable memory.zip from the repo.

Thin stand-in for the eventual builder: it bundles the engine (the ``librarian``
package) and a ``reference/`` slot into a single ZIP that a code-interpreter host
can boot. Optionally seeds initial KB content.

NOTE: the master prompt (``MASTER_PROMPT.md``) is deliberately NOT bundled — it
is pasted into the agent builder's instructions field, so it is a *separate*
deliverable that lives outside the ZIP.

    python scripts/build_memory.py [out.zip] [--seed DIR] [--wheelhouse DIR]
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import sys

# allow running straight from a checkout/unpack — no install needed
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))

from librarian.store import pack_zip

REPO = Path(__file__).resolve().parent.parent
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.tmp")

# --- the thin agent factory: profiles are DATA, never engine code -------------
# A profile = a prompt overlay (+ optional KB seed) over the one shared engine.
# The registry is DERIVED from profiles/, never a hardcoded list.
PROFILES_DIR = REPO / "profiles"
_PROMPT_SLOTS = (   # (fragment stem, marker in profiles/_base/MASTER_PROMPT.md)
    ("intro", "{{PROFILE_INTRO}}"),
    ("operations", "{{PROFILE_OPERATIONS}}"),
    ("cheatsheet", "{{PROFILE_CHEATSHEET}}"),
)

# --- AST Apex backend: one friendly --ast flag instead of a wall of pip flags ----
# The canonical wheel set lives HERE (single source; mirrors README §1.B). The
# 0.13.0 pin is REQUIRED — the last language-pack release that bundles grammars in
# the wheel; pack 1.x downloads them at runtime (impossible in the offline sandbox,
# and build() refuses it). The builder slims the pack to the apex grammar only
# (~20 MB -> ~0.3 MB) unless --no-slim.
_AST_WHEEL_SPECS = (
    "tree-sitter>=0.25.2,<1",
    "tree-sitter-language-pack==0.13.0",
    "pypdf>=4,<7",
)
# friendly target presets -> (pip --platform tag, --python-version). The wheels
# must match the SANDBOX, not the build machine — that is the whole point of the
# presets. Default is the common Azure-style linux x86_64 / py3.12 sandbox.
_AST_TARGETS = {
    "linux-x64-py312": ("manylinux2014_x86_64", "312"),
    "linux-x64-py311": ("manylinux2014_x86_64", "311"),
    "linux-x64-py310": ("manylinux2014_x86_64", "310"),
    "linux-arm64-py312": ("manylinux2014_aarch64", "312"),
    "linux-arm64-py311": ("manylinux2014_aarch64", "311"),
}
_AST_DEFAULT_TARGET = "linux-x64-py312"

# What --wheelhouse is FOR: the optional tree-sitter AST Apex backend. The
# vendored engine runs stdlib-only (regex backend) by default; bundling the
# wheels below lets bootstrap.boot() pip-install them offline in the sandbox,
# and the engine's runtime probe then upgrades Apex parsing automatically
# (constructor refs, instance-call resolution). Download wheels matching the
# SANDBOX's platform/Python — e.g. for a linux x86_64 / Python 3.12 host:
#
#   pip download --only-binary :all: --platform manylinux2014_x86_64 \
#       --python-version 312 -d wheelhouse/ \
#       "tree-sitter>=0.25.2,<1" "tree-sitter-language-pack==0.13.0" "pypdf>=4,<7"
#
# The ==0.13.0 pin is REQUIRED for offline sandboxes: the last release bundling
# all grammars in the wheel. Pack 1.x fetches grammars from GitHub on first
# use — impossible without network. 0.13's property-style node API is handled
# by the engine's compatibility shim.
#
# The builder slims the pack wheel to the apex grammar by default (~20 MB ->
# ~0.3 MB; --no-slim keeps all grammars). pypdf enables the PDF digest.
# (Version caps mirror the engine's `ast`/`pdf` extras — see vendor/README.md.)
# Wrong-platform wheels fail the boot-time install harmlessly: the engine
# falls back to the regex backend, exactly as without a wheelhouse.


def _slim_language_pack(src, dest, keep="apex"):
    """Rewrite a language-pack 0.x wheel keeping ONLY the ``keep`` grammar.

    The 0.x wheels bundle ~100 compiled grammars (verilog alone is 17 MB); the
    agent parses exactly one. Stripping ``bindings/*.so`` down to the one we use
    cuts the wheel ~20 MB -> ~0.3 MB — which also shrinks every boot install and
    every checkpoint pack. The wheel RECORD is regenerated (pip verifies per-file
    sha256 on install); METADATA is untouched, so the pack's tiny grammar-dep
    wheels (c_sharp/embedded_template/yaml — imported by its __init__) must stay
    in the wheelhouse. Returns (orig_mb, slim_mb)."""
    import base64, csv, hashlib, io

    src, dest = Path(src), Path(dest)
    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        dist_info = next(n for n in names if n.endswith("/METADATA")).rsplit("/", 1)[0]
        out = io.BytesIO()
        rows = []
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for n in names:
                if "/bindings/" in n and not n.endswith(f"/{keep}.abi3.so"):
                    continue
                if n.endswith("/RECORD"):
                    continue
                data = zin.read(n)
                zout.writestr(n, data)
                digest = base64.urlsafe_b64encode(
                    hashlib.sha256(data).digest()).rstrip(b"=").decode()
                rows.append((n, f"sha256={digest}", str(len(data))))
            rec = io.StringIO()
            writer = csv.writer(rec)
            for row in rows:
                writer.writerow(row)
            writer.writerow((f"{dist_info}/RECORD", "", ""))
            zout.writestr(f"{dist_info}/RECORD", rec.getvalue())
    dest.write_bytes(out.getvalue())
    return src.stat().st_size / 1048576, dest.stat().st_size / 1048576


def build(dest="memory.zip", seed_dir=None, wheelhouse=None, slim=True) -> Path:
    staging = Path(tempfile.mkdtemp()) / "mem"
    staging.mkdir(parents=True)

    # the engine — travels inside the ZIP so the agent imports it after unpack
    shutil.copytree(REPO / "librarian", staging / "librarian", ignore=_IGNORE)

    # the vendored Salesforce parsing engine — shipped at the ZIP root so the
    # digest adapter can `import graphbuilder` after unpack (bootstrap.boot() puts
    # the unpacked root on sys.path). See librarian/digest/graphbuilder.py and
    # vendor/README.md.
    gb_src = REPO / "vendor" / "graphbuilder"
    if gb_src.is_dir():
        shutil.copytree(gb_src, staging / "graphbuilder", ignore=_IGNORE)

    # NOTE: MASTER_PROMPT.md is intentionally NOT included — it is pasted into the
    # agent builder's instructions field and lives outside the ZIP.

    # reference assets slot (Polish lemmas, wheelhouse)
    (staging / "reference").mkdir(exist_ok=True)
    if wheelhouse:
        wh = Path(wheelhouse)
        wheels = sorted(wh.glob("*.whl")) if wh.is_dir() else []
        if not wheels:
            raise SystemExit(
                f"--wheelhouse {wheelhouse}: no *.whl files found.\n"
                "Download the AST-backend wheels into it first (match the "
                "SANDBOX's platform/Python, not this machine's), e.g. for a "
                "linux x86_64 / Python 3.12 sandbox:\n\n"
                f"  python3 -m pip download --only-binary :all: "
                "--platform manylinux2014_x86_64 \\\n"
                f"      --python-version 312 -d {wheelhouse} \\\n"
                "      \"tree-sitter>=0.25.2,<1\" \"tree-sitter-language-pack==0.13.0\" "
                "\"pypdf>=4,<7\"\n\n"
                "Or build WITHOUT --wheelhouse — the agent then uses the "
                "always-on regex Apex backend.")
        # keep only the NEWEST wheel per package — `pip download -d` appends,
        # so a reused dir accumulates old versions, and bundling two versions
        # of one package makes the boot-time install unresolvable
        newest: dict = {}
        for w in wheels:
            name, ver = w.name.split("-")[0], w.name.split("-")[1]
            key = tuple(int(x) for x in __import__("re").findall(r"\d+", ver))
            if name not in newest or key > newest[name][0]:
                if name in newest:
                    print(f"wheelhouse: dropping older duplicate {newest[name][1].name}")
                newest[name] = (key, w)
            else:
                print(f"wheelhouse: dropping older duplicate {w.name}")
        wheels = [w for _, w in newest.values()]
        for w in wheels:   # the known offline trap, loudly
            if w.name.startswith("tree_sitter_language_pack-") \
                    and not w.name.startswith("tree_sitter_language_pack-0."):
                raise SystemExit(
                    f"wheelhouse bundles {w.name}: language-pack >=1 DOWNLOADS "
                    "grammars from GitHub on first use and cannot work in an "
                    "offline sandbox. Delete the wheelhouse dir and re-download "
                    "with the pinned command from README §1.B "
                    "(tree-sitter-language-pack==0.13.0).")
        dest_wh = staging / "reference" / "wheelhouse"
        dest_wh.mkdir(parents=True)
        for w in wheels:
            if slim and w.name.startswith("tree_sitter_language_pack-0."):
                before, after = _slim_language_pack(w, dest_wh / w.name, keep="apex")
                print(f"wheelhouse: slimmed {w.name.split('-')[0]} to apex-only "
                      f"({before:.1f} MB -> {after:.1f} MB; --no-slim keeps all grammars)")
            else:
                shutil.copy2(w, dest_wh / w.name)
        print(f"Apex backend in this zip: AST ({len(wheels)} wheels bundled)")
    else:
        print("Apex backend in this zip: regex — no wheelhouse bundled "
              "(pass --wheelhouse DIR for the AST upgrade; see README §1.B)")

    if seed_dir:
        shutil.copytree(seed_dir, staging, dirs_exist_ok=True)

    return pack_zip(staging, dest)


def list_profiles() -> list:
    """Available profiles, DERIVED from the profiles/ directory — never a
    hardcoded list. A profile is any subdir of profiles/ other than _base."""
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(p.name for p in PROFILES_DIR.iterdir()
                  if p.is_dir() and not p.name.startswith("_"))


def assemble_prompt(profile) -> str:
    """Fill the shared base contract's overlay markers with a profile's
    prompt/<slot>.md fragments. A missing fragment leaves its marker empty (its
    line is removed cleanly). The assembled prompt is what ships beside the ZIP."""
    base_path = PROFILES_DIR / "_base" / "MASTER_PROMPT.md"
    if not base_path.is_file():
        raise SystemExit(f"missing base prompt: {base_path}")
    out = base_path.read_text("utf-8")
    pdir = PROFILES_DIR / profile / "prompt"
    for slot, marker in _PROMPT_SLOTS:
        frag_path = pdir / f"{slot}.md"
        frag = frag_path.read_text("utf-8").strip() if frag_path.is_file() else ""
        if frag:
            # Each marker sits ALONE on its line; swapping just the token for the
            # fragment keeps the surrounding blank lines exactly as authored. No
            # blank-run collapse is needed — so fenced code blocks (the cheatsheet
            # slot lives inside one) are never reflowed, and a fragment may carry
            # its own blank lines safely.
            out = out.replace(marker, frag)
        else:   # drop the unused marker's whole line + one trailing blank line
            out = re.sub(rf"^[ \t]*{re.escape(marker)}[ \t]*\n\n?", "", out,
                         flags=re.MULTILINE)
    if "{{PROFILE_" in out:
        raise SystemExit(f"profile {profile!r}: an unfilled {{PROFILE_*}} marker "
                         "remains in the assembled prompt")
    return out


def build_profile(profile, out_dir=None, wheelhouse=None, slim=True):
    """Build one agent variant: a CLEAN memory.zip (engine + optional seed, no
    ingested data) plus the assembled MASTER_PROMPT.md BESIDE it (never inside
    the ZIP). Returns (out_dir, memzip_path, prompt_path)."""
    available = list_profiles()
    if profile not in available:
        raise SystemExit(f"unknown profile {profile!r}; available: "
                         f"{', '.join(available) or '(none)'}")
    out_dir = Path(out_dir) if out_dir else (REPO / "dist" / profile)
    out_dir.mkdir(parents=True, exist_ok=True)
    seed = PROFILES_DIR / profile / "seed"
    seed_dir = str(seed) if seed.is_dir() and any(seed.iterdir()) else None
    memzip = build(out_dir / "memory.zip", seed_dir=seed_dir,
                   wheelhouse=wheelhouse, slim=slim)
    prompt_path = out_dir / "MASTER_PROMPT.md"
    prompt_path.write_text(assemble_prompt(profile), "utf-8")
    return out_dir, memzip, prompt_path


def _download_ast_wheels(target=None) -> Path:
    """Download the tree-sitter AST + pypdf wheels for the SANDBOX platform into a
    fresh temp dir and return it (to pass as the wheelhouse). This is what ``--ast``
    runs: it hides the platform tags, the version pins, and the always-use-a-clean-
    dir rule. Needs network AT BUILD TIME (the deployed sandbox stays offline)."""
    target = target or _AST_DEFAULT_TARGET
    if target not in _AST_TARGETS:
        raise SystemExit(
            f"--ast target {target!r} not known. Choices: "
            f"{', '.join(sorted(_AST_TARGETS))}. "
            "(If you meant an output path, put it before --ast or use "
            "--out-dir/--profile; or use --wheelhouse DIR with your own wheels.)")
    plat, pyver = _AST_TARGETS[target]
    wh = Path(tempfile.mkdtemp(prefix="ast_wheels_"))   # fresh dir: no append trap
    cmd = [sys.executable, "-m", "pip", "download", "--only-binary", ":all:",
           "--platform", plat, "--python-version", pyver, "-d", str(wh),
           *_AST_WHEEL_SPECS]
    print(f"--ast {target}: downloading AST wheels for {plat} / py{pyver} …")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        shutil.rmtree(wh, ignore_errors=True)
        raise SystemExit(
            f"--ast {target}: pip could not fetch the AST wheels for this platform.\n"
            f"  {' '.join(cmd)}\n\n{res.stderr.strip()}\n\n"
            "Check the target matches your sandbox, or download the wheels yourself "
            "and use --wheelhouse (see README §1.B).")
    return wh


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="memory.zip")
    ap.add_argument("--profile", default=None,
                    help="build a named agent variant from profiles/<name>/ "
                         "(emits dist/<name>/memory.zip + its assembled MASTER_PROMPT.md)")
    ap.add_argument("--list-profiles", action="store_true",
                    help="list the available profiles (derived from profiles/) and exit")
    ap.add_argument("--out-dir", default=None,
                    help="output directory for a --profile build (default: dist/<name>/)")
    ap.add_argument("--seed", default=None, help="directory of initial KB content to include")
    ap.add_argument("--no-slim", action="store_true",
                    help="bundle the language-pack wheel with ALL grammars "
                         "(default slims it to apex-only; see _slim_language_pack)")
    ap.add_argument("--wheelhouse", default=None,
                    help="directory of *.whl files to bundle for offline install at boot "
                         "(use: the tree-sitter AST backend; see module docstring)")
    ap.add_argument("--ast", nargs="?", const=_AST_DEFAULT_TARGET, default=None,
                    metavar="TARGET",
                    help="bundle the tree-sitter AST Apex backend (+pypdf), auto-downloading "
                         "the right wheels for the SANDBOX — the easy alternative to "
                         f"--wheelhouse. Optional target (default {_AST_DEFAULT_TARGET}); "
                         f"choices: {', '.join(sorted(_AST_TARGETS))}.")
    args = ap.parse_args()

    if args.list_profiles:
        for name in list_profiles():
            print(name)
        raise SystemExit(0)

    if args.ast and args.wheelhouse:
        raise SystemExit("--ast and --wheelhouse are mutually exclusive — --ast IS the "
                         "wheelhouse (it downloads it for you).")
    wheelhouse, _ast_tmp = args.wheelhouse, None
    if args.ast:
        _ast_tmp = _download_ast_wheels(args.ast)
        wheelhouse = str(_ast_tmp)

    try:
        if args.profile:
            out_dir, memzip, prompt_path = build_profile(
                args.profile, args.out_dir, wheelhouse, slim=not args.no_slim)
            print(f"built profile '{args.profile}':")
            print(f"  memory.zip     {memzip}")
            print(f"  MASTER_PROMPT  {prompt_path}")
            print("deploy: paste MASTER_PROMPT.md into the agent builder's instructions "
                  "field, then upload memory.zip. The prompt is NOT inside the ZIP.")
        else:
            out = build(args.out, args.seed, wheelhouse, slim=not args.no_slim)
            print(f"built {out}")
            print("reminder: paste MASTER_PROMPT.md into the agent builder's instructions "
                  "field — it is NOT inside the ZIP.")
    finally:
        if _ast_tmp:
            shutil.rmtree(_ast_tmp, ignore_errors=True)
