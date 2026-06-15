"""describe_assets.py — ingest assets into the skill catalog.

Two-folder asset layout:
  - SVGs live inside the skill at skill/assets/   (small, gitignored binaries,
    yaml descriptions committed alongside).
  - Photos/raster binaries live OUTSIDE the skill in an external folder
    (../assets-external/ by default — too heavy for the skill).

For each binary found in <source_dir>:
  - .svg  → moved into skill/assets/; yaml written there.
  - raster → left in source_dir; yaml written to skill/assets/.

Yaml fields fall into two buckets:

  Mechanical (always re-derived from the binary on every run):
    id, file, kind, width, height, aspect, recommended_slot, sha1

  Descriptive (NEVER overwritten if the yaml already exists — your LLM
  vision pass and manual edits are preserved):
    tags, description, alt_text, subject, depicts, feel, composition,
    colors, colors_hex, scope, suitable_for, status, notes, interpretation,
    is, sources, … any field not in the mechanical set.

Use --force-redescribe to wipe descriptive fields and rebuild from scratch.

Usage:
  python describe_assets.py /path/to/source/
  python describe_assets.py /path/to/source/ --force-redescribe
  python describe_assets.py /path/to/source/ --with-vision-prompts
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import yaml
from PIL import Image


PROMPT_PATH = Path(__file__).parent / "prompts" / "describe_asset.md"
VOCAB_PATH = Path(__file__).parent / "asset_tag_vocab.yaml"
DEFAULT_SKILL_ASSETS = Path(__file__).parent / "assets"


_RASTER_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}
_VECTOR_EXTS = {".svg"}

# Fields the binary owns — always re-derived, always overwrite existing.
_MECHANICAL_FIELDS = {
    "id", "file", "kind",
    "width", "height", "aspect", "recommended_slot", "sha1",
}

# Cell aspect ratio. Canvas 13.333" × 7.5" divides into 13 col-units × 14
# row-units, so 1 col-unit = 1.026" and 1 row-unit = 0.536" → cells are
# ~1.91× wider than tall. recommend_slot solves: pick (col_span, row_span)
# such that (col_span * 1.026) / (row_span * 0.536) ≈ asset_aspect.
_CELL_ASPECT = (13.333 / 13) / (7.5 / 14)


def _guess_kind(ext: str) -> str:
    e = ext.lower()
    if e in _RASTER_EXTS:
        return "photo"
    if e == ".svg":
        return "pictogram"
    return "asset"


def _file_sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _dominant_colors(path: Path, n: int = 5) -> list[str]:
    try:
        img = Image.open(path).convert("RGB")
        img.thumbnail((128, 128))
        quant = img.quantize(colors=n, method=Image.Quantize.MEDIANCUT)
        palette = quant.getpalette()
        if not palette:
            return []
        triples = [palette[i * 3:(i + 1) * 3] for i in range(n)]
        return [f"#{r:02X}{g:02X}{b:02X}" for r, g, b in triples
                if (r, g, b) != (0, 0, 0)][:n]
    except Exception:
        return []


def _raster_metrics(path: Path) -> dict:
    try:
        img = Image.open(path)
        w, h = img.size
        return {
            "width": w,
            "height": h,
            "aspect": round(w / h, 3) if h else None,
        }
    except Exception:
        return {"width": None, "height": None, "aspect": None}


def _svg_metrics(path: Path) -> dict:
    """Parse intrinsic width/height from SVG viewBox or explicit attributes."""
    import re
    try:
        text = path.read_text(errors="ignore")[:4000]
    except Exception:
        return {"width": None, "height": None, "aspect": None}
    w = h = None
    m = re.search(r'viewBox\s*=\s*"([^"]+)"', text)
    if m:
        parts = m.group(1).strip().split()
        if len(parts) >= 4:
            try:
                w = float(parts[2])
                h = float(parts[3])
            except ValueError:
                pass
    if w is None:
        mw = re.search(r'\bwidth\s*=\s*"([0-9.]+)', text)
        mh = re.search(r'\bheight\s*=\s*"([0-9.]+)', text)
        if mw and mh:
            try:
                w = float(mw.group(1))
                h = float(mh.group(1))
            except ValueError:
                pass
    return {
        "width": int(w) if w else None,
        "height": int(h) if h else None,
        "aspect": round(w / h, 3) if (w and h) else None,
    }


def _recommended_slot(asset_aspect: float) -> dict | None:
    """Find (col_span, row_span) whose grid-slot aspect best matches the
    asset's aspect. Biased toward medium total size (~8 cells)."""
    if not asset_aspect or asset_aspect <= 0:
        return None
    target_ratio = asset_aspect / _CELL_ASPECT
    best = None
    best_err = float("inf")
    for col in range(1, 13):
        for row in range(1, 13):
            total = col + row
            if total < 4 or total > 12:
                continue
            ratio_err = abs(col / row - target_ratio)
            size_penalty = abs(total - 8) * 0.01
            err = ratio_err + size_penalty
            if err < best_err:
                best_err = err
                best = (col, row)
    if not best:
        return None
    return {"col_span": best[0], "row_span": best[1]}


def _build_mechanical(binary: Path) -> dict:
    """All fields derived purely from the binary file."""
    fresh: dict = {
        "id": binary.stem,
        "file": binary.name,
        "kind": _guess_kind(binary.suffix),
    }
    ext = binary.suffix.lower()
    if ext in _RASTER_EXTS:
        fresh.update(_raster_metrics(binary))
    elif ext == ".svg":
        fresh.update(_svg_metrics(binary))
    if fresh.get("aspect"):
        fresh["recommended_slot"] = _recommended_slot(fresh["aspect"])
    fresh["sha1"] = _file_sha1(binary)
    return fresh


def _merge(existing: dict, fresh: dict, force_redescribe: bool) -> dict:
    """Merge fresh (mechanical) into existing (descriptive).

    On force_redescribe, descriptive fields are wiped — use only when you
    really want to regenerate descriptions from scratch.
    """
    if force_redescribe or not existing:
        # New asset or force rebuild — seed descriptive defaults for LLM pass.
        out = dict(fresh)
        out.setdefault("tags", [])
        out.setdefault("description", "")
        out.setdefault("status", "pending")
        return out
    # Preserve every existing field; only mechanical fields are overwritten.
    out = dict(existing)
    for k, v in fresh.items():
        if k in _MECHANICAL_FIELDS:
            out[k] = v
    return out


def _load_prompt() -> str:
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text()
    return "(no prompt template found — see prompts/describe_asset.md)"


def _load_vocab() -> list[str]:
    if VOCAB_PATH.exists():
        data = yaml.safe_load(VOCAB_PATH.read_text()) or {}
        return data.get("tags") or []
    return []


def describe(
    source_dir: str,
    skill_assets_dir: Path | None = None,
    force_redescribe: bool = False,
    with_vision_prompts: bool = False,
) -> dict:
    """Ingest assets from source_dir.

    Returns {written: [yaml paths], moved_svgs: [svg paths], new: [binaries]}.
    """
    if skill_assets_dir is None:
        skill_assets_dir = DEFAULT_SKILL_ASSETS
    skill_assets_dir = Path(skill_assets_dir)
    skill_assets_dir.mkdir(parents=True, exist_ok=True)

    source = Path(source_dir).resolve()
    if not source.exists():
        print(f"source dir not found: {source_dir}", file=sys.stderr)
        return {"written": [], "moved_svgs": [], "new": []}

    prompt = _load_prompt() if with_vision_prompts else None
    vocab = _load_vocab() if with_vision_prompts else None

    written: list[Path] = []
    moved: list[Path] = []
    new_assets: list[Path] = []

    for binary in sorted(source.iterdir()):
        if not binary.is_file():
            continue
        ext = binary.suffix.lower()
        if ext not in (_RASTER_EXTS | _VECTOR_EXTS):
            continue

        yaml_path = skill_assets_dir / f"{binary.stem}.yaml"
        existing: dict = {}
        if yaml_path.exists():
            try:
                existing = yaml.safe_load(yaml_path.read_text()) or {}
            except yaml.YAMLError:
                pass
        is_new = not existing

        fresh = _build_mechanical(binary)
        # colors_hex is descriptive-ish: only auto-fill for new yamls or when
        # missing from existing. User/LLM can override.
        if ext in _RASTER_EXTS and not existing.get("colors_hex"):
            fresh["colors_hex"] = _dominant_colors(binary)

        merged = _merge(existing, fresh, force_redescribe)
        yaml_path.write_text(yaml.safe_dump(merged, sort_keys=False))
        written.append(yaml_path)

        # SVGs move into the skill so the agent can read them as text and so
        # the renderer can embed them as native vector. Photos stay put.
        if ext == ".svg":
            target = skill_assets_dir / binary.name
            if binary.resolve() != target.resolve():
                # Path.replace overwrites atomically if target exists.
                binary.replace(target)
                moved.append(target)

        if is_new:
            new_assets.append(binary)

    if with_vision_prompts and new_assets:
        print("=" * 60, file=sys.stderr)
        print(f"VOCAB: {vocab}", file=sys.stderr)
        print("PROMPT:", file=sys.stderr)
        print(prompt, file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print("NEW ASSETS — paste each into a vision LLM and edit its yaml:",
              file=sys.stderr)
        for b in new_assets:
            yp = skill_assets_dir / f"{b.stem}.yaml"
            print(f"  - binary: {b}", file=sys.stderr)
            print(f"    yaml:   {yp}", file=sys.stderr)

    return {"written": written, "moved_svgs": moved, "new": new_assets}


def _cli():
    p = argparse.ArgumentParser(prog="describe_assets")
    p.add_argument("source_dir",
                   help="Folder containing newly dropped assets to ingest.")
    p.add_argument("--skill-assets", default=None,
                   help="Where yamls land (and where SVGs get moved). "
                        "Defaults to the skill's bundled assets/ folder.")
    p.add_argument("--force-redescribe", action="store_true",
                   help="Wipe descriptive fields and rebuild from scratch. "
                        "Off by default — descriptions are preserved.")
    p.add_argument("--with-vision-prompts", action="store_true",
                   help="After ingest, print the describer prompt for any "
                        "NEW assets that still need LLM descriptions.")
    args = p.parse_args()

    out = describe(
        args.source_dir,
        skill_assets_dir=args.skill_assets,
        force_redescribe=args.force_redescribe,
        with_vision_prompts=args.with_vision_prompts,
    )
    print(
        f"wrote {len(out['written'])} sidecar(s), "
        f"moved {len(out['moved_svgs'])} SVG(s), "
        f"{len(out['new'])} new",
        file=sys.stderr,
    )


if __name__ == "__main__":
    _cli()
