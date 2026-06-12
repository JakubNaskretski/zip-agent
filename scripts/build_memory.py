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
import shutil
import tempfile
from pathlib import Path

import sys

# allow running straight from a checkout/unpack — no install needed
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))

from librarian.store import pack_zip

REPO = Path(__file__).resolve().parent.parent
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.tmp")

# What --wheelhouse is FOR: the optional tree-sitter AST Apex backend. The
# vendored engine runs stdlib-only (regex backend) by default; bundling the
# wheels below lets bootstrap.boot() pip-install them offline in the sandbox,
# and the engine's runtime probe then upgrades Apex parsing automatically
# (constructor refs, instance-call resolution). Download wheels matching the
# SANDBOX's platform/Python — e.g. for a linux x86_64 / Python 3.12 host:
#
#   pip download --only-binary :all: --platform manylinux2014_x86_64 \
#       --python-version 312 -d wheelhouse/ \
#       "tree-sitter>=0.25.2,<1" "tree-sitter-language-pack==0.13.0"
#
# The ==0.13.0 pin is REQUIRED for offline sandboxes: the last release bundling
# all grammars in the wheel. Pack 1.x fetches grammars from GitHub on first
# use — impossible without network. 0.13's property-style node API is handled
# by the engine's compatibility shim.
#
# (Version caps mirror the engine's `ast` extra — see vendor/README.md.)
# Wrong-platform wheels fail the boot-time install harmlessly: the engine
# falls back to the regex backend, exactly as without a wheelhouse.


def build(dest="memory.zip", seed_dir=None, wheelhouse=None) -> Path:
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
                "--platform manylinux_2_34_x86_64 --platform manylinux2014_x86_64 \\\n"
                f"      --python-version 312 -d {wheelhouse} \\\n"
                "      \"tree-sitter>=0.25,<1\" \"tree-sitter-language-pack>=1,<2\"\n\n"
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
            shutil.copy2(w, dest_wh / w.name)
        print(f"Apex backend in this zip: AST ({len(wheels)} wheels bundled)")
    else:
        print("Apex backend in this zip: regex — no wheelhouse bundled "
              "(pass --wheelhouse DIR for the AST upgrade; see README §1.B)")

    if seed_dir:
        shutil.copytree(seed_dir, staging, dirs_exist_ok=True)

    return pack_zip(staging, dest)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="memory.zip")
    ap.add_argument("--seed", default=None, help="directory of initial KB content to include")
    ap.add_argument("--wheelhouse", default=None,
                    help="directory of *.whl files to bundle for offline install at boot "
                         "(use: the tree-sitter AST backend; see module docstring)")
    args = ap.parse_args()
    out = build(args.out, args.seed, args.wheelhouse)
    print(f"built {out}")
    print("reminder: paste MASTER_PROMPT.md into the agent builder's instructions "
          "field — it is NOT inside the ZIP.")
