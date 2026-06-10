"""Assemble a deployable memory.zip from the repo.

Thin stand-in for the eventual builder: it bundles the engine (the ``librarian``
package) and a ``reference/`` slot into a single ZIP that a code-interpreter host
can boot. Optionally seeds initial KB content.

NOTE: the master prompt (``MASTER_PROMPT.md``) is deliberately NOT bundled — it
is pasted into the agent builder's instructions field, so it is a *separate*
deliverable that lives outside the ZIP.

    python scripts/build_memory.py [out.zip] [--seed DIR]
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from librarian.store import pack_zip

REPO = Path(__file__).resolve().parent.parent
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.tmp")


def build(dest="memory.zip", seed_dir=None) -> Path:
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

    # reference assets slot (Polish lemmas, wheelhouse) — created empty for now
    (staging / "reference").mkdir(exist_ok=True)

    if seed_dir:
        shutil.copytree(seed_dir, staging, dirs_exist_ok=True)

    return pack_zip(staging, dest)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="memory.zip")
    ap.add_argument("--seed", default=None, help="directory of initial KB content to include")
    args = ap.parse_args()
    out = build(args.out, args.seed)
    print(f"built {out}")
    print("reminder: paste MASTER_PROMPT.md into the agent builder's instructions "
          "field — it is NOT inside the ZIP.")
