"""Upgrade a deployed memory.zip to a new code build — without losing knowledge.

The memory ZIP is code + state in one file. Shipping a new engine build must
not cost the agent its already-ingested knowledge, so this tool splits the two:

  STATE (carried from OLD)          CODE + ASSETS (taken from NEW)
  ------------------------          ------------------------------
  kb/**            (all tiers,      librarian/      (the engine)
                    minus indexes)  graphbuilder/   (vendored SF/Mule engine)
  manifest.json    (minus index     reference/      (lemmas, wheelhouse)
                    entries)        ...any other non-state top-level
  dev/changelog.json
  dev/session_state.json (I11)

  DROPPED: kb/indexes/** and their manifest entries (tier "indexes"). Derived
  indexes are 100% rebuildable (invariant I13) and the new code may carry a
  newer index schema — so they are rebuilt, never migrated.

This is owner-side offline tooling: it never modifies its inputs, and the
output is written atomically (temp + os.replace, via the same ``pack_zip`` the
engine uses for I12). It refuses to downgrade: if OLD's manifest_version is
newer than what NEW's bundled code supports, nothing is written.

    python3 scripts/upgrade_memory.py OLD_memory.zip NEW_code.zip -o upgraded.zip

After first boot of the upgraded ZIP the agent MUST rebuild the indexes
(``from librarian import rebuild_indexes``) — the script reminds you, loudly.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import sys

# allow running straight from a checkout/unpack — no install needed
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))

from librarian.store import pack_zip

# The state/code split (docs/ARCHITECTURE.md §2; store.py defines the state
# file locations). Everything NOT matching these prefixes is code/assets.
STATE_DIRS = ("kb/", "dev/")
STATE_FILES = ("manifest.json",)
INDEX_DIR = "kb/indexes/"          # librarian/index.py INDEX_PATH lives here
INDEX_TIER = "indexes"


def _is_state(name: str) -> bool:
    return name in STATE_FILES or name.startswith(STATE_DIRS)


def _version_key(v) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v))) or (0,)


def _supported_manifest_version(new_zf: zipfile.ZipFile) -> str:
    """The MANIFEST_VERSION pinned inside the NEW zip's own code — what the
    upgraded artifact will actually run, which may differ from this checkout."""
    try:
        text = new_zf.read("librarian/manifest.py").decode("utf-8", "replace")
    except KeyError:
        raise SystemExit(
            "NEW zip has no librarian/manifest.py — is it really a code build "
            "from scripts/build_memory.py?")
    m = re.search(r"""MANIFEST_VERSION\s*=\s*["']([^"']+)["']""", text)
    if not m:
        raise SystemExit(
            "could not read MANIFEST_VERSION out of the NEW zip's "
            "librarian/manifest.py — refusing to guess compatibility")
    return m.group(1)


def _code_pin(new_zf: zipfile.ZipFile):
    """Best-effort provenance of the NEW build (the vendored engine pin)."""
    try:
        text = new_zf.read("librarian/digest/graphbuilder.py").decode("utf-8", "replace")
    except KeyError:
        return None
    m = re.search(r"""_VENDORED_SHA\s*=\s*["']([^"']+)["']""", text)
    return m.group(1) if m else None


def upgrade(old_zip, new_zip, out="upgraded.zip") -> Path:
    """Carry the STATE out of ``old_zip``, the CODE out of ``new_zip``, drop
    the derived indexes (I13), and write the merged ZIP atomically to ``out``.
    Inputs are never modified."""
    old_zip, new_zip, out = Path(old_zip), Path(new_zip), Path(out)
    for p, label in ((old_zip, "OLD"), (new_zip, "NEW")):
        if not p.is_file():
            raise SystemExit(f"{label} zip not found: {p}")
    if out.resolve() in (old_zip.resolve(), new_zip.resolve()):
        raise SystemExit("output path must not overwrite an input zip")

    tmp_root = Path(tempfile.mkdtemp(prefix="upgrade_memory_"))
    staging = tmp_root / "mem"
    staging.mkdir(parents=True)
    try:
        # ---- CODE + ASSETS from NEW (everything that is not state) ----
        with zipfile.ZipFile(new_zip) as zf:
            supported = _supported_manifest_version(zf)
            pin = _code_pin(zf)
            names = [n for n in zf.namelist() if not n.endswith("/")]
            ignored_new_state = sorted(n for n in names if _is_state(n))
            for n in names:
                if not _is_state(n):
                    zf.extract(n, staging)
            code_top = sorted({n.split("/", 1)[0] for n in names if not _is_state(n)})
        if ignored_new_state:
            print(f"note: NEW zip carries {len(ignored_new_state)} state file(s) "
                  f"(e.g. {ignored_new_state[0]}) — ignored; state comes from OLD")

        # ---- STATE from OLD (kb/**, manifest.json, dev/**) ----
        with zipfile.ZipFile(old_zip) as zf:
            try:
                manifest = json.loads(zf.read("manifest.json"))
            except KeyError:
                raise SystemExit(
                    "OLD zip has no manifest.json — it holds no agent state. "
                    "Nothing to upgrade; deploy the NEW zip directly.")
            old_version = manifest.get("manifest_version", "0")
            if _version_key(old_version) > _version_key(supported):
                raise SystemExit(
                    f"REFUSING to upgrade: OLD manifest_version {old_version} is "
                    f"NEWER than the {supported} supported by the code in "
                    f"{new_zip.name}. That would silently downgrade the "
                    "knowledge schema. Build a newer code zip first.")

            dropped_files = 0
            for n in zf.namelist():
                if n.endswith("/") or not _is_state(n) or n in STATE_FILES:
                    continue
                if n.startswith(INDEX_DIR):
                    dropped_files += 1          # derived; rebuildable (I13)
                    continue
                zf.extract(n, staging)

        # filter the derived index entries out of the carried manifest —
        # the new code rebuilds them with its own (possibly newer) schema
        resources = manifest.get("resources", [])
        kept, dropped_kus = [], []
        for r in resources:
            if r.get("tier") == INDEX_TIER or str(r.get("path", "")).startswith(INDEX_DIR):
                dropped_kus.append(r.get("id", "?"))
            else:
                kept.append(r)
        manifest["resources"] = kept
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")

        result = pack_zip(staging, out)         # atomic: temp + os.replace (I12)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    by_tier: dict = {}
    for r in kept:
        t = r.get("tier", "?")
        by_tier[t] = by_tier.get(t, 0) + 1
    tiers = ", ".join(f"{t}={n}" for t, n in sorted(by_tier.items())) or "none"
    print("upgrade summary")
    print(f"  state from OLD : {len(kept)} KUs carried ({tiers}), "
          f"manifest {old_version}, generation {manifest.get('generation', '?')}")
    print(f"  dropped (I13)  : {len(dropped_kus)} derived index KU(s) "
          f"{dropped_kus or ''} + {dropped_files} file(s) under {INDEX_DIR}")
    print(f"  code from NEW  : {', '.join(code_top)}"
          + (f" (engine pin {pin})" if pin else ""))
    print(f"  wrote          : {result}")
    print()
    print("=" * 64)
    print("  REMINDER — the derived indexes were NOT carried over (I13).")
    print("  After FIRST BOOT of the upgraded zip the agent must run:")
    print("      from librarian import rebuild_indexes")
    print('      rebuild_indexes(lib, author, "rebuild indexes after engine upgrade")')
    print("  There is no search index until it does.")
    print("=" * 64)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Merge the knowledge state of an OLD memory.zip into a NEW code build.")
    ap.add_argument("old_zip", help="deployed memory.zip holding the knowledge to keep")
    ap.add_argument("new_zip", help="fresh code build from scripts/build_memory.py")
    ap.add_argument("-o", "--out", default="upgraded.zip",
                    help="where to write the merged zip (default: upgraded.zip)")
    args = ap.parse_args()
    upgrade(args.old_zip, args.new_zip, args.out)
