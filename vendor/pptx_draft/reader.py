"""reader.py — agent-facing API surface.

CLI commands (use stdout JSON for machine-readability):

  python reader.py theme
  python reader.py list-recipes
  python reader.py recipe-signature <name>
  python reader.py preview-recipe <name> --content '<json>' [--params '<json>']

  python reader.py cell-to-rect --row R --col C [--row-span N] [--col-span N]

  python reader.py measure-text <text> --type-level <name> --cell-rect '<json>'
  python reader.py check-asset-fit --asset '<json>' --cell-rect '<json>' [--fit fill]
  python reader.py contrast-check <fg_hex> <bg_hex>
  python reader.py palette-audit --components '<json>'
  python reader.py grid-audit --components '<json>'
  python reader.py visual-balance --components '<json>'
  python reader.py deck-flow <plan.json>
  python reader.py chart-sanity --content '<json>'

  python reader.py asset-index [--asset-dir DIR] [--external-dir DIR]
  python reader.py tag-summary [--asset-dir DIR] [--external-dir DIR]
  python reader.py read-assets [<asset_dir>] [--external-dir DIR]
  python reader.py find-asset [<asset_dir>] [--kind photo] [--tags people,office]
                              [--limit 5] [--external-dir DIR]
  python reader.py preview-asset <asset_id> [--asset-dir DIR] [--external-dir DIR]
  python reader.py opener-template-status

  python reader.py validate-plan <plan.json>

JSON inputs can come from string args or from a file path (if the arg starts
with @, the rest is treated as a path).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

import grid as grid_mod
from recipes import RECIPES, RECIPE_SIGNATURES
from components import COMPONENT_RENDERERS
from toolbelt import (
    measure_text as _measure_text,
    check_asset_fit as _check_asset_fit,
    contrast_check as _contrast_check,
    palette_audit as _palette_audit,
    grid_audit as _grid_audit,
    visual_balance as _visual_balance,
    deck_flow as _deck_flow,
    chart_sanity as _chart_sanity,
)


THEME_PATH = Path(__file__).parent / "theme.yaml"
DEFAULT_ASSETS_DIR = Path(__file__).parent / "assets"
# External photo folder — sibling of the repo root by convention. Holds raster
# binaries (jpg/png) that are too heavy to ship inside the skill. SVGs live in
# DEFAULT_ASSETS_DIR.
DEFAULT_EXTERNAL_ASSETS_DIR = Path(__file__).parent.parent.parent / "assets-external"


# ---------- helpers ----------


def _load_theme() -> dict:
    with open(THEME_PATH) as f:
        return yaml.safe_load(f)


def _load_json_arg(value: str):
    """Parse a JSON argument. If it starts with @, treat as a path."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if value.startswith("@"):
        with open(value[1:]) as f:
            return json.load(f)
    return json.loads(value)


def _print_json(obj):
    print(json.dumps(obj, indent=2, default=str))


# ---------- module-level API (also usable from imports) ----------


def theme() -> dict:
    return _load_theme()


def list_recipes() -> list[dict]:
    return [{"name": name, **RECIPE_SIGNATURES.get(name, {})} for name in RECIPES]


def recipe_signature(name: str) -> dict:
    if name not in RECIPES:
        raise ValueError(f"unknown recipe: {name}")
    return {"name": name, **RECIPE_SIGNATURES.get(name, {})}


def preview_recipe(name: str, content: dict, params: dict | None = None) -> list[dict]:
    """Resolve a recipe to its component placements (without rendering)."""
    if name not in RECIPES:
        raise ValueError(f"unknown recipe: {name}")
    return RECIPES[name](content, **(params or {}))


def list_components() -> list[str]:
    return sorted(COMPONENT_RENDERERS.keys())


def cell_to_rect(row: int, col: int, row_span: int = 1, col_span: int = 1) -> dict:
    return grid_mod.cell_to_rect(row, col, row_span, col_span)


def measure_text(text: str, type_level: str, cell_rect: dict) -> dict:
    t = _load_theme()
    canvas = t.get("canvas", {})
    return _measure_text(
        text,
        type_level=type_level,
        cell_rect=cell_rect,
        canvas_w_in=canvas.get("width_in", 13.333),
        canvas_h_in=canvas.get("height_in", 7.5),
        theme=t,
    )


def check_asset_fit(asset: dict, cell_rect: dict, fit_mode: str = "fill") -> dict:
    t = _load_theme()
    canvas = t.get("canvas", {})
    return _check_asset_fit(
        asset,
        cell_rect,
        canvas_w_in=canvas.get("width_in", 13.333),
        canvas_h_in=canvas.get("height_in", 7.5),
        fit_mode=fit_mode,
    )


def contrast_check(fg_hex: str, bg_hex: str) -> dict:
    return _contrast_check(fg_hex, bg_hex)


def palette_audit(slide_components: list[dict]) -> dict:
    return _palette_audit(slide_components, _load_theme())


def grid_audit(slide_components: list[dict]) -> dict:
    return _grid_audit(slide_components)


def visual_balance(slide_components: list[dict]) -> dict:
    return _visual_balance(slide_components)


def deck_flow(plan: dict) -> dict:
    return _deck_flow(plan)


def chart_sanity(content: dict) -> dict:
    return _chart_sanity(content)


# ---------- asset directory reads ----------


_ASSET_BINARY_SUFFIXES = {".jpg", ".jpeg", ".png", ".svg", ".gif", ".webp"}
_VECTOR_SUFFIXES = {".svg"}


def read_assets(
    asset_dir: str | None = None,
    external_dir: str | None = None,
) -> list[dict]:
    """Walk `asset_dir`, return parsed sidecar yamls.

    Two-folder layout:
      - asset_dir (default: skill/assets/) holds sidecar yamls + SVG binaries.
      - external_dir (default: ../../assets-external/) holds raster photo
        binaries. Raster yamls live in asset_dir but the binary is resolved
        against external_dir.

    Each returned entry includes `abs_path` — the resolved location of the
    binary. For raster files we don't check existence here (the binary may
    only live on the user's machine, not in the sandbox). Use preview_asset
    for an actual existence check.
    """
    if asset_dir is None:
        asset_dir = str(DEFAULT_ASSETS_DIR)
    root = Path(asset_dir)
    if not root.exists() or not root.is_dir():
        return []
    ext_root = Path(external_dir) if external_dir else DEFAULT_EXTERNAL_ASSETS_DIR

    out = []
    for yaml_path in sorted(root.glob("*.yaml")):
        if yaml_path.name in {"theme.yaml", "asset_tag_vocab.yaml"}:
            continue
        try:
            with open(yaml_path) as f:
                data = yaml.safe_load(f) or {}
        except yaml.YAMLError:
            continue
        if "id" not in data:
            data["id"] = yaml_path.stem
        file = data.get("file")
        if file and not Path(file).is_absolute():
            ext = Path(file).suffix.lower()
            if ext in _VECTOR_SUFFIXES:
                data["abs_path"] = str((root / file).resolve())
            else:
                # Raster: prefer external dir; fall back to skill assets dir
                # so the logo case (raster inside the skill) still resolves.
                ext_path = ext_root / file
                skill_path = root / file
                if ext_path.exists():
                    data["abs_path"] = str(ext_path.resolve())
                elif skill_path.exists():
                    data["abs_path"] = str(skill_path.resolve())
                else:
                    # Binary missing on this machine — return the expected
                    # external location so the user can see where to drop it.
                    data["abs_path"] = str(ext_path.resolve())
        out.append(data)
    return out


def find_asset(
    asset_dir: str | None = None,
    kind: str | None = None,
    tags: list[str] | None = None,
    limit: int = 10,
    external_dir: str | None = None,
) -> dict:
    """Filter assets in `asset_dir` by kind + tags (AND). Returns up to `limit`.

    Broadening: if tags filter yields zero, retry without tags (one step only).
    If `asset_dir` is None, scans the skill's bundled assets/ folder.
    """
    assets = read_assets(asset_dir, external_dir=external_dir)
    matches = []
    for a in assets:
        if kind and a.get("kind") != kind:
            continue
        if tags:
            asset_tags = set(a.get("tags") or [])
            if not all(t in asset_tags for t in tags):
                continue
        matches.append(a)

    broadened = False
    if not matches and tags:
        # Retry without tags
        broadened = True
        for a in assets:
            if kind and a.get("kind") != kind:
                continue
            matches.append(a)

    matches = matches[:limit]
    return {
        "count": len(matches),
        "broadened": broadened,
        "matches": matches,
    }


def asset_index(
    asset_dir: str | None = None,
    external_dir: str | None = None,
) -> dict:
    """Return a compact id→summary map of every asset in the catalog.

    For agents that want to scan the catalog once and filter locally
    rather than making repeated find-asset queries. Each entry keeps
    only the fields useful for picking:

        kind, description, tags, aspect, recommended_slot, previewable

    Heavy fields (sha1, colors_hex, raw width/height) and per-machine
    fields (abs_path) are omitted — call preview-asset for the file
    path of an SVG you want to inspect.

    `previewable: true` ⟺ the binary is a `.svg` and lives inside the
    skill (readable as XML via your file-reading tool).
    """
    out: dict[str, dict] = {}
    for a in read_assets(asset_dir, external_dir=external_dir):
        asset_id = a.get("id")
        if not asset_id:
            continue
        file = (a.get("file") or "").lower()
        out[asset_id] = {
            "kind": a.get("kind"),
            "description": a.get("description") or "",
            "tags": a.get("tags") or [],
            "aspect": a.get("aspect"),
            "recommended_slot": a.get("recommended_slot"),
            "previewable": file.endswith(".svg"),
        }
    return out


def tag_summary(
    asset_dir: str | None = None,
    external_dir: str | None = None,
) -> dict:
    """Return total counts + per-kind + per-tag histograms for the catalog.

    Useful before you craft find-asset queries — shows you the actual
    vocabulary instead of guessing tag names.

    Returns:
      {
        "total": int,                 # number of assets in the catalog
        "kinds": {name: count, ...},  # sorted desc by count
        "tags":  {name: count, ...},  # sorted desc by count
      }
    """
    kind_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    assets = read_assets(asset_dir, external_dir=external_dir)
    for a in assets:
        kind = a.get("kind") or "unknown"
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        for tag in (a.get("tags") or []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    return {
        "total": len(assets),
        "kinds": dict(sorted(kind_counts.items(), key=lambda kv: -kv[1])),
        "tags":  dict(sorted(tag_counts.items(),  key=lambda kv: -kv[1])),
    }


def opener_template_status() -> dict:
    """Report whether a pre-rendered opening slide template is active.

    Returns:
      {
        "enabled": bool,    # USE_TEMPLATE_OPENER constant in render.py
        "exists": bool,     # whether the .pptx file is on disk
        "path": str,        # absolute path the renderer looks at
        "effective": bool,  # True iff enabled AND exists (will actually be used)
      }

    Call during outline review (Phase 2). If `effective` is True, the
    renderer will prepend the template slide as slide 1 of every deck
    unless the plan opts out via `use_template_opener: false`.
    """
    try:
        from render import USE_TEMPLATE_OPENER, TEMPLATE_OPENER_PATH
    except ImportError:
        return {
            "enabled": False, "exists": False, "path": None,
            "effective": False,
            "reason": "render.py not importable",
        }
    template_path = Path(__file__).parent / TEMPLATE_OPENER_PATH
    exists = template_path.exists()
    return {
        "enabled": bool(USE_TEMPLATE_OPENER),
        "exists": exists,
        "path": str(template_path),
        "effective": bool(USE_TEMPLATE_OPENER) and exists,
    }


def preview_asset(
    asset_id: str,
    asset_dir: str | None = None,
    external_dir: str | None = None,
) -> dict:
    """Return whether/how to preview an asset visually.

    For SVG matches: the binary lives inside the skill (`skill/assets/`) and
    is plain XML text — agent should Read `abs_path` to see the actual shape
    before committing.

    For raster matches: binaries live outside the skill in the external photo
    folder. They're not directly visible to the agent (and often too heavy
    even if they were) — pick by `description` / `tags` from find-asset.
    """
    for a in read_assets(asset_dir, external_dir=external_dir):
        if a.get("id") != asset_id:
            continue
        file = a.get("file") or ""
        ext = Path(file).suffix.lower()
        abs_path = a.get("abs_path")
        if ext in _VECTOR_SUFFIXES:
            if abs_path and Path(abs_path).exists():
                return {
                    "available": True,
                    "format": "svg",
                    "abs_path": abs_path,
                    "hint": ("SVG is plain text — Read this abs_path with "
                             "your file-reading tool to inspect the shape."),
                }
            return {
                "available": False,
                "format": "svg",
                "reason": f"SVG binary missing on disk (expected at {abs_path}).",
            }
        return {
            "available": False,
            "format": ext.lstrip(".") or "unknown",
            "reason": ("Raster previews are not exposed to the agent. The "
                       "binary lives outside the skill in the external photo "
                       "folder. Pick by description and tags from find-asset."),
        }
    return {
        "available": False,
        "reason": f"asset_id '{asset_id}' not found in {asset_dir or DEFAULT_ASSETS_DIR}.",
    }


# ---------- single-slide and whole-plan validation ----------


_TEXT_BEARING = ("heading", "text", "quote")


def _extract_text_for_measure(placement: dict) -> tuple[str, str]:
    """Extract (text, type_level) for measure_text on a text-bearing placement.

    Returns (None, None) if no measurable text content.
    """
    ptype = placement.get("type")
    content = placement.get("content")
    if ptype == "quote":
        if isinstance(content, dict):
            text = content.get("text", "")
        else:
            text = str(content) if content else ""
        level = placement.get("level", "quote")
    elif ptype in ("heading", "text"):
        level = placement.get("level", "body" if ptype == "text" else "h1")
        if isinstance(content, list):
            text = "\n".join(str(c) for c in content)
        elif content is None:
            text = ""
        else:
            text = str(content)
    else:
        return None, None
    return (text, level) if text else (None, None)


def validate_slide(slide_spec: dict, theme: dict | None = None) -> dict:
    """Validate ONE slide draft.

    Consolidates:
      - recipe resolution (errors out if unknown or build fails)
      - grid_audit            (ERROR on overlaps / out-of-bounds)
      - measure_text          (per text-bearing component, tiered severity)
      - palette_audit         (WARNING on off-palette colors)
      - chart_sanity          (WARNING on chart-type / data mismatches)

    Severity for text overflow:
      lines <= max_lines             -> ok (no entry)
      lines == max_lines + 1         -> WARNING (one line over — may be acceptable)
      lines >  max_lines + 1         -> ERROR (multi-line overflow drowns the slide)

    Returns {ok, slide_id, errors, warnings}. `ok` is False iff any error.
    """
    if theme is None:
        theme = _load_theme()
    canvas = theme.get("canvas", {})
    canvas_w = canvas.get("width_in", 13.333)
    canvas_h = canvas.get("height_in", 7.5)

    slide_id = slide_spec.get("id", "?")
    recipe_name = slide_spec.get("recipe")

    errors: list[dict] = []
    warnings: list[dict] = []

    # 1. Resolve placements
    if recipe_name not in RECIPES and recipe_name != "free":
        return {
            "ok": False,
            "slide_id": slide_id,
            "errors": [{
                "kind": "unknown_recipe",
                "slide_id": slide_id,
                "message": f"unknown recipe: {recipe_name}",
            }],
            "warnings": [],
        }
    if recipe_name == "free":
        placements = slide_spec.get("components") or []
    else:
        try:
            placements = RECIPES[recipe_name](
                slide_spec.get("content") or {},
                **(slide_spec.get("params") or {}),
            )
        except Exception as e:
            return {
                "ok": False,
                "slide_id": slide_id,
                "errors": [{
                    "kind": "recipe_error",
                    "slide_id": slide_id,
                    "message": f"recipe {recipe_name} raised: {e}",
                }],
                "warnings": [],
            }

    # 2. Grid audit
    ga = _grid_audit(placements)
    if not ga["ok"]:
        errors.append({"kind": "grid_audit", "slide_id": slide_id, "details": ga})

    # 3. Text overflow per heading/text/quote
    for idx, p in enumerate(placements):
        if p.get("type") not in _TEXT_BEARING:
            continue
        text, level = _extract_text_for_measure(p)
        if not text:
            continue
        try:
            from grid import placement_to_rect
            rect = placement_to_rect(p["grid"])
        except (KeyError, ValueError):
            continue
        try:
            m = _measure_text(
                text,
                type_level=level,
                cell_rect=rect,
                canvas_w_in=canvas_w,
                canvas_h_in=canvas_h,
                theme=theme,
            )
        except KeyError:
            continue  # unknown type_level
        if m["fits"]:
            continue
        overflow_lines = m["lines"] - m["max_lines"]
        entry = {
            "slide_id": slide_id,
            "component_index": idx,
            "component_type": p.get("type"),
            "type_level": level,
            "lines": m["lines"],
            "max_lines": m["max_lines"],
            "overflow_chars": m["overflow_chars"],
            "suggested_size_pt": m["suggested_size_pt"],
            "suggestion": m["suggestion"],
        }
        if overflow_lines <= 1:
            warnings.append({"kind": "text_overflow_minor", **entry})
        else:
            errors.append({"kind": "text_overflow_major", **entry})

    # 4. Palette audit
    pa = _palette_audit(placements, theme)
    if not pa["ok"]:
        warnings.append({"kind": "palette_audit", "slide_id": slide_id, "details": pa})

    # 5. Chart sanity per chart
    for idx, p in enumerate(placements):
        if p.get("type") != "chart":
            continue
        cs = _chart_sanity(p.get("content") or {})
        if not cs["ok"]:
            warnings.append({
                "kind": "chart_sanity",
                "slide_id": slide_id,
                "component_index": idx,
                "details": cs,
            })

    # 6. Metric value overflow check.
    # metric components render value at 48pt with word_wrap=False, so an
    # overlong value bleeds horizontally into the neighbor cell. Catch it.
    from grid import placement_to_rect
    for idx, p in enumerate(placements):
        if p.get("type") != "metric":
            continue
        content = p.get("content") or {}
        value = str(content.get("value", "") or "")
        if not value:
            continue
        try:
            rect = placement_to_rect(p["grid"])
        except (KeyError, ValueError):
            continue
        # Value cell uses full grid-cell width and ~55% of its height
        value_rect = {
            "x": rect["x"], "y": rect["y"],
            "w": rect["w"], "h": rect["h"] * 0.55,
        }
        try:
            m = _measure_text(
                value, type_level="metric_value", cell_rect=value_rect,
                canvas_w_in=canvas_w, canvas_h_in=canvas_h, theme=theme,
            )
        except KeyError:
            continue
        # word_wrap=False on metric values — any horizontal overflow bleeds
        # into the neighbor cell. Treat chars > chars_per_line as ERROR.
        if len(value) > m["chars_per_line"]:
            errors.append({
                "kind": "metric_value_overflow",
                "slide_id": slide_id,
                "component_index": idx,
                "value": value,
                "chars": len(value),
                "max_chars_at_48pt": m["chars_per_line"],
                "suggestion": (
                    f"Value '{value}' is {len(value)} chars; cell fits "
                    f"~{m['chars_per_line']} at 48pt. Shorten "
                    f"(e.g. '$1.2M' instead of '$1,234,567') or use fewer "
                    f"metrics in the strip."
                ),
            })

    return {
        "ok": len(errors) == 0,
        "slide_id": slide_id,
        "errors": errors,
        "warnings": warnings,
    }


def validate_plan(plan: dict) -> dict:
    """Run validate_slide on each slide + deck_flow at the end."""
    errors: list[dict] = []
    warnings: list[dict] = []

    if not isinstance(plan, dict):
        return {"ok": False, "errors": [{"message": "plan must be a dict"}], "warnings": []}

    if plan.get("version") != "1":
        warnings.append({"kind": "version", "message": "plan version != '1'"})

    slides = plan.get("slides") or []
    if not slides:
        errors.append({"kind": "empty", "message": "plan has no slides"})
        return {"ok": False, "errors": errors, "warnings": warnings}

    theme = _load_theme()  # Load once, pass to each validate_slide call
    for slide in slides:
        result = validate_slide(slide, theme=theme)
        errors.extend(result["errors"])
        warnings.extend(result["warnings"])

    # Deck-level narrative checks
    df = _deck_flow(plan)
    if not df["ok"]:
        for issue in df["issues"]:
            warnings.append({"kind": f"deck_flow.{issue['kind']}", "details": issue})

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}


# ---------- CLI ----------


def _cli():
    p = argparse.ArgumentParser(prog="reader", description="pptx-grid-skill agent API")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("theme")
    sub.add_parser("list-recipes")
    sub.add_parser("list-components")

    r = sub.add_parser("recipe-signature")
    r.add_argument("name")

    r = sub.add_parser("preview-recipe")
    r.add_argument("name")
    r.add_argument("--content", required=True, help="JSON or @path")
    r.add_argument("--params", default="{}")

    r = sub.add_parser("cell-to-rect")
    r.add_argument("--row", type=int, required=True)
    r.add_argument("--col", type=int, required=True)
    r.add_argument("--row-span", type=int, default=1)
    r.add_argument("--col-span", type=int, default=1)

    r = sub.add_parser("measure-text")
    r.add_argument("text")
    r.add_argument("--type-level", required=True)
    r.add_argument("--cell-rect", required=True, help="JSON or @path")

    r = sub.add_parser("check-asset-fit")
    r.add_argument("--asset", required=True)
    r.add_argument("--cell-rect", required=True)
    r.add_argument("--fit", default="fill")

    r = sub.add_parser("contrast-check")
    r.add_argument("fg")
    r.add_argument("bg")

    r = sub.add_parser("palette-audit")
    r.add_argument("--components", required=True)

    r = sub.add_parser("grid-audit")
    r.add_argument("--components", required=True)

    r = sub.add_parser("visual-balance")
    r.add_argument("--components", required=True)

    r = sub.add_parser("deck-flow")
    r.add_argument("plan")

    r = sub.add_parser("chart-sanity")
    r.add_argument("--content", required=True)

    r = sub.add_parser("asset-index",
                       help="Compact id→summary map of the entire catalog.")
    r.add_argument("--asset-dir", default=None,
                   help="path to assets directory (default: bundled assets/)")
    r.add_argument("--external-dir", default=None,
                   help="external raster folder (default: ../../assets-external/)")

    r = sub.add_parser("tag-summary",
                       help="Total + per-kind + per-tag counts for the catalog.")
    r.add_argument("--asset-dir", default=None,
                   help="path to assets directory (default: bundled assets/)")
    r.add_argument("--external-dir", default=None,
                   help="external raster folder (default: ../../assets-external/)")

    r = sub.add_parser("read-assets")
    r.add_argument("asset_dir", nargs="?", default=None,
                   help="path to assets directory (default: bundled assets/)")
    r.add_argument("--external-dir", default=None,
                   help="external raster folder (default: ../../assets-external/)")

    r = sub.add_parser("find-asset")
    r.add_argument("asset_dir", nargs="?", default=None,
                   help="path to assets directory (default: bundled assets/)")
    r.add_argument("--kind", default=None)
    r.add_argument("--tags", default=None, help="comma-separated")
    r.add_argument("--limit", type=int, default=10)
    r.add_argument("--external-dir", default=None,
                   help="external raster folder (default: ../../assets-external/)")

    r = sub.add_parser("preview-asset")
    r.add_argument("asset_id")
    r.add_argument("--asset-dir", default=None,
                   help="path to assets directory (default: bundled assets/)")
    r.add_argument("--external-dir", default=None,
                   help="external raster folder (default: ../../assets-external/)")

    sub.add_parser("opener-template-status",
                   help="Report whether a pre-rendered opener template is active.")

    r = sub.add_parser("validate-slide")
    r.add_argument("slide", help="slide.json (a single slide spec)")

    r = sub.add_parser("validate-plan")
    r.add_argument("plan")

    args = p.parse_args()

    if args.command == "theme":
        _print_json(theme())
    elif args.command == "list-recipes":
        _print_json(list_recipes())
    elif args.command == "list-components":
        _print_json(list_components())
    elif args.command == "recipe-signature":
        _print_json(recipe_signature(args.name))
    elif args.command == "preview-recipe":
        content = _load_json_arg(args.content)
        params = _load_json_arg(args.params)
        _print_json(preview_recipe(args.name, content, params))
    elif args.command == "cell-to-rect":
        _print_json(cell_to_rect(args.row, args.col, args.row_span, args.col_span))
    elif args.command == "measure-text":
        _print_json(measure_text(args.text, args.type_level, _load_json_arg(args.cell_rect)))
    elif args.command == "check-asset-fit":
        _print_json(check_asset_fit(_load_json_arg(args.asset),
                                    _load_json_arg(args.cell_rect),
                                    args.fit))
    elif args.command == "contrast-check":
        _print_json(contrast_check(args.fg, args.bg))
    elif args.command == "palette-audit":
        _print_json(palette_audit(_load_json_arg(args.components)))
    elif args.command == "grid-audit":
        _print_json(grid_audit(_load_json_arg(args.components)))
    elif args.command == "visual-balance":
        _print_json(visual_balance(_load_json_arg(args.components)))
    elif args.command == "deck-flow":
        plan = _load_json_arg(f"@{args.plan}") if not args.plan.startswith("{") else _load_json_arg(args.plan)
        _print_json(deck_flow(plan))
    elif args.command == "chart-sanity":
        _print_json(chart_sanity(_load_json_arg(args.content)))
    elif args.command == "asset-index":
        _print_json(asset_index(asset_dir=args.asset_dir,
                                external_dir=args.external_dir))
    elif args.command == "tag-summary":
        _print_json(tag_summary(asset_dir=args.asset_dir,
                                external_dir=args.external_dir))
    elif args.command == "read-assets":
        _print_json(read_assets(args.asset_dir, external_dir=args.external_dir))
    elif args.command == "find-asset":
        tags = args.tags.split(",") if args.tags else None
        _print_json(find_asset(args.asset_dir, kind=args.kind, tags=tags,
                               limit=args.limit, external_dir=args.external_dir))
    elif args.command == "preview-asset":
        _print_json(preview_asset(args.asset_id,
                                  asset_dir=args.asset_dir,
                                  external_dir=args.external_dir))
    elif args.command == "opener-template-status":
        _print_json(opener_template_status())
    elif args.command == "validate-slide":
        slide = _load_json_arg(f"@{args.slide}")
        _print_json(validate_slide(slide))
    elif args.command == "validate-plan":
        plan = _load_json_arg(f"@{args.plan}")
        _print_json(validate_plan(plan))


if __name__ == "__main__":
    _cli()
