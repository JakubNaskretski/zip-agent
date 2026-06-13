"""Extract an agent's knowledge state out of a deployed memory.zip.

The memory ZIP is code + state in one file. This pulls ONLY the state
(``kb/**`` + ``manifest.json`` + ``dev/**``) into a separate bundle — to back up
a KB, to keep a knowledge base you care about, or as the input side of an engine
upgrade (the same state/code split ``scripts/upgrade_memory.py`` uses).

It never modifies its input and writes its output atomically (temp + os.replace,
via the same ``pack_zip`` the engine uses for I12). Derived indexes
(``kb/indexes/**``) are excluded by default — they are 100% rebuildable (I13) and
carry no irreplaceable state; pass ``--with-indexes`` to keep them anyway.

    python3 scripts/extract_kb.py DEPLOYED_memory.zip -o kb-bundle.zip

There is deliberately NO profile<->profile migration: this extracts a KB for
backup or same-lineage upgrade, not to turn one agent type into another.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import sys

# allow running straight from a checkout/unpack — no install needed
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))

from librarian.store import pack_zip

# The state/code split (docs/ARCHITECTURE.md §2; mirrors scripts/upgrade_memory.py).
STATE_DIRS = ("kb/", "dev/")
STATE_FILES = ("manifest.json",)
INDEX_DIR = "kb/indexes/"          # derived; rebuildable (I13)


def _is_state(name: str) -> bool:
    return name in STATE_FILES or name.startswith(STATE_DIRS)


def extract(src_zip, out="kb-bundle.zip", with_indexes=False) -> Path:
    """Carry the STATE out of ``src_zip`` into ``out``; the input is never
    modified. Returns the path written."""
    src, out = Path(src_zip), Path(out)
    if not src.is_file():
        raise SystemExit(f"memory zip not found: {src}")
    if out.resolve() == src.resolve():
        raise SystemExit("output path must not overwrite the input zip")

    tmp_root = Path(tempfile.mkdtemp(prefix="extract_kb_"))
    staging = tmp_root / "kb-bundle"
    staging.mkdir(parents=True)
    by_tier: dict = {}
    dropped_index = 0
    try:
        with zipfile.ZipFile(src) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            if "manifest.json" not in names:
                raise SystemExit(
                    f"{src.name} has no manifest.json — it holds no agent state. "
                    "Nothing to extract (it is a code-only build).")
            for n in names:
                if not _is_state(n):
                    continue
                if not with_indexes and n.startswith(INDEX_DIR):
                    dropped_index += 1
                    continue
                zf.extract(n, staging)
        # tally carried state by tier WHILE staging still exists (before rmtree)
        mpath = staging / "manifest.json"
        if mpath.is_file():
            try:
                manifest = json.loads(mpath.read_text("utf-8"))
                for r in manifest.get("resources", manifest.get("kus", [])):
                    t = r.get("tier", "?")
                    by_tier[t] = by_tier.get(t, 0) + 1
            except Exception:
                pass
        result = pack_zip(staging, out)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    tiers = ", ".join(f"{t}={n}" for t, n in sorted(by_tier.items())) or "n/a"
    print("extract summary")
    print(f"  source : {src}")
    print(f"  state  : {tiers}")
    if dropped_index and not with_indexes:
        print(f"  dropped: {dropped_index} derived index file(s) under {INDEX_DIR} "
              "(rebuildable — I13; pass --with-indexes to keep)")
    print(f"  wrote  : {result}")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Extract the knowledge state (kb/ + manifest + dev/) from a memory.zip.")
    ap.add_argument("memory_zip", help="a deployed memory.zip holding the knowledge to extract")
    ap.add_argument("-o", "--out", default="kb-bundle.zip",
                    help="where to write the extracted KB bundle (default: kb-bundle.zip)")
    ap.add_argument("--with-indexes", action="store_true",
                    help="also carry the derived kb/indexes/** (default: drop them, they rebuild)")
    args = ap.parse_args()
    extract(args.memory_zip, args.out, with_indexes=args.with_indexes)
