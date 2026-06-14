"""pptx-skill consumer reader (v5).

Read-side CLI an agent calls against a built bundle (or the authoring
workspace during dev):

  python reader.py list-themes
  python reader.py list-skeletons [--category c] [--has-slot kind]
  python reader.py get-skeleton <id> / get-theme <id>
  python reader.py match-skeletons --content '<json>' [--category c]
  python reader.py find-asset --kind k [--tags t]...
  python reader.py check-asset-fit <asset_id> <skeleton_id> <slot_id>
  python reader.py measure-text <text> [--against <skeleton>.<slot>]
  python reader.py validate-plan <plan.json>
  python reader.py compose-v5 <plan.json> <out.pptx> --theme <id> [--force]

All commands write JSON to stdout (compose-v5 also writes the deck).
Read-only commands need only PyYAML; python-pptx is required (and
imported) only by compose-v5. No state. No vision required. Read
SKILL.md for the contract.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml


HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Bundle layout helpers
# ---------------------------------------------------------------------------


def bundle_root() -> Path:
    return HERE


def load_index() -> dict:
    p = bundle_root() / "index.json"
    if not p.exists():
        raise SystemExit(f"index.json not found at {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _drop_first_slide(prs: Presentation) -> None:
    """Remove the host's original first slide after appending plan slides.

    Used when a compose-mode entry is the first plan entry — we still
    need a host pptx (for its theme/master), but we don't want its
    original first slide to leak into the output.
    """
    try:
        from pptx.oxml.ns import qn
    except ImportError:
        return
    sld_id_list = prs.slides._sldIdLst
    sld_ids = list(sld_id_list)
    if not sld_ids:
        return
    first = sld_ids[0]
    rid = first.get(qn("r:id"))
    sld_id_list.remove(first)
    if rid:
        try:
            prs.part.drop_rel(rid)
        except KeyError:
            pass


# ---------------------------------------------------------------------------


# ===========================================================================
# v5 redesign — read-side methods (phase D)
#
# Self-contained block. Operates on either a built v5 bundle (next to
# reader.py with themes/ + skeletons/ + assets/ siblings, per phase F)
# OR directly on workspace/themes/ + workspace/skeletons/ during dev
# (so we can exercise the API end-to-end before phase E + F ship).
#
# Read-only — no deck building. Phase E owns compose-v5.
# ===========================================================================


def _v5_bundle_root() -> Path:
    """Find the v5 data root. Tries built-bundle layout first
    (themes/skeletons siblings next to reader.py), falls back to the
    authoring workspace for dev. Returns None if neither is present.
    """
    here = bundle_root()
    if (here / "themes").is_dir() and (here / "skeletons").is_dir():
        return here
    # Dev fallback: repo/authoring/workspace/
    ws = here.parent / "authoring" / "workspace"
    if (ws / "themes").is_dir() and (ws / "skeletons").is_dir():
        return ws
    return here  # caller will see empty results


def _v5_themes_dir() -> Path:
    return _v5_bundle_root() / "themes"


def _v5_skeletons_dir() -> Path:
    return _v5_bundle_root() / "skeletons"


def _v5_assets_dir() -> Path:
    # Assets live next to themes/skeletons in workspace; in a built
    # bundle they're under "assets/" sibling. Same path either way.
    return _v5_bundle_root() / "assets"


def _v5_load_yaml(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None


def _v5_iter_skeletons() -> list[dict]:
    """Walk every skeleton in the bundle. Supports both layouts:
      flat:   skeletons/<id>.yaml         (brief bundle — text only)
      nested: skeletons/<id>/skeleton.yaml (offline build-v5 bundle — has
                                            siblings like preview.png)
    """
    root = _v5_skeletons_dir()
    if not root.exists():
        return []
    out: list[dict] = []
    for entry in sorted(root.iterdir()):
        if entry.is_file() and entry.suffix == ".yaml":
            sk = _v5_load_yaml(entry)
        elif entry.is_dir():
            sk = _v5_load_yaml(entry / "skeleton.yaml")
        else:
            continue
        if sk:
            out.append(sk)
    return out


def _v5_load_skeleton(skeleton_id: str) -> dict | None:
    # Flat layout (brief bundle) first; nested fallback (offline build-v5).
    flat = _v5_skeletons_dir() / f"{skeleton_id}.yaml"
    if flat.exists():
        return _v5_load_yaml(flat)
    return _v5_load_yaml(_v5_skeletons_dir() / skeleton_id / "skeleton.yaml")


def _v5_load_theme(theme_id: str) -> dict | None:
    # Flat layout (brief bundle) first; nested fallback (offline build-v5).
    flat = _v5_themes_dir() / f"{theme_id}.yaml"
    if flat.exists():
        return _v5_load_yaml(flat)
    return _v5_load_yaml(_v5_themes_dir() / theme_id / "theme.yaml")


def _v5_load_asset_meta(asset_id: str) -> dict | None:
    return _v5_load_yaml(_v5_assets_dir() / f"{asset_id}.yaml")


# --- Constraint helpers (single source of truth for fit logic) -------------


def _v5_check_text_fit(text: str, constraints: dict) -> tuple[bool, str, int]:
    """Returns (fits, reason, headroom_chars). Headroom positive = under
    constraint, negative = over.
    """
    max_chars = constraints.get("max_chars")
    if max_chars is None:
        return True, "", 0
    n = len(text or "")
    headroom = max_chars - n
    if headroom < 0:
        return False, f"{n} chars > max_chars {max_chars}", headroom
    return True, "", headroom


def _v5_check_bullets_fit(items: list, constraints: dict) -> tuple[bool, str, dict]:
    max_items = constraints.get("max_items")
    max_chars_per_item = constraints.get("max_chars_per_item")
    n_items = len(items or [])
    headroom = {"items": (max_items - n_items) if max_items is not None else 0}
    if max_items is not None and n_items > max_items:
        return False, f"{n_items} items > max_items {max_items}", headroom
    if max_chars_per_item is not None:
        for i, it in enumerate(items or []):
            if len(str(it)) > max_chars_per_item:
                return (
                    False,
                    f"item {i} has {len(str(it))} chars > max_chars_per_item {max_chars_per_item}",
                    headroom,
                )
    return True, "", headroom


def _v5_check_table_fit(table_dict: dict, constraints: dict) -> tuple[bool, str, dict]:
    rows = table_dict.get("rows", 0)
    cols = table_dict.get("cols", 0)
    max_rows = constraints.get("max_rows")
    max_cols = constraints.get("max_cols")
    headroom = {
        "rows": (max_rows - rows) if max_rows is not None else 0,
        "cols": (max_cols - cols) if max_cols is not None else 0,
    }
    if max_rows is not None and rows > max_rows:
        return False, f"{rows} rows > max_rows {max_rows}", headroom
    if max_cols is not None and cols > max_cols:
        return False, f"{cols} cols > max_cols {max_cols}", headroom
    return True, "", headroom


def _v5_check_image_fit(asset_meta: dict, constraints: dict) -> tuple[bool, str, dict]:
    transform = {"will_crop": False, "will_resize": False}
    slot_aspect = (constraints.get("aspect") or "free").lower()
    if slot_aspect == "free":
        return True, "", transform
    aspect_targets = {"1:1": 1.0, "16:9": 16/9, "4:3": 4/3, "3:4": 3/4, "9:16": 9/16}
    target = aspect_targets.get(slot_aspect)
    if target is None:
        return True, f"unknown aspect {slot_aspect!r} — letting through", transform
    w = asset_meta.get("width") or asset_meta.get("dimensions", {}).get("width") or 0
    h = asset_meta.get("height") or asset_meta.get("dimensions", {}).get("height") or 0
    if w <= 0 or h <= 0:
        return True, "asset dims unknown — best-effort fit", transform
    asset_aspect = w / h
    if abs(asset_aspect - target) / target > 0.05:
        transform["will_crop"] = True
        return False, f"asset aspect {asset_aspect:.2f} vs slot {slot_aspect} ({target:.2f}); would crop", transform
    return True, "", transform


# --- Slot lookup helpers ---------------------------------------------------


_CONTENT_KEY_TO_KIND = {
    "title": "heading",
    "heading": "heading",
    "subtitle": "heading",
    "paragraph": "paragraph",
    "bullets": "bullets",
    "image": "image",
    "hero": "image",
    "table": "table",
    "chart": "chart",
    "footer": "footer",
}

# v5.1 — role preference in slot mapping. When the agent uses a content
# key that names a role, prefer slots with matching role over first-of-kind.
# Flip _V5_ENABLE_ROLE_MATCHING to False to disable purely on the reader
# side without touching the ingest output.
_V5_ENABLE_ROLE_MATCHING = True

_CONTENT_KEY_TO_ROLE = {
    "title": "page_title",
    "page_title": "page_title",
    "subtitle": "subtitle",
    "body": "body",
    "footer": "footer",
    "footnote": "footnote",
    "caption": "caption",
    "key_points": "key_points",
    "detailed_list": "detailed_list",
    "cta": "cta",
    "section_header": "section_header",
}


def _v5_first_slot_of_kind(skeleton: dict, kind: str, exclude_ids: set[str]) -> dict | None:
    for s in skeleton.get("slots") or []:
        if s.get("kind") == kind and s.get("id") not in exclude_ids:
            return s
    return None


def _v5_first_slot_by_role(skeleton: dict, role: str, exclude_ids: set[str]) -> dict | None:
    for s in skeleton.get("slots") or []:
        if s.get("role") == role and s.get("id") not in exclude_ids:
            return s
    return None


def _v5_infer_kinds_from_value(value: Any) -> list[str]:
    """Deterministic fallback for content keys outside the explicit
    key→kind/role maps: infer candidate slot kinds from the VALUE's
    shape. Pure function — same value, same answer. Returns the kinds
    to try, in order."""
    if isinstance(value, list):
        return ["bullets"]
    if isinstance(value, dict):
        if "rows" in value or "data" in value:
            return ["table"]
        if "series" in value or "categories" in value:
            return ["chart"]
        if value.get("placeholder") or "asset" in value or "asset_id" in value:
            return ["image"]
        if "value" in value:
            return ["heading", "paragraph"]
        return []
    if isinstance(value, str):
        if value.startswith("asset_") or value == "placeholder":
            return ["image"]
        return ["heading", "paragraph"]
    return []


def _v5_build_slot_mapping(content: dict, skeleton: dict) -> tuple[dict, list[str]]:
    """Map content keys to slot ids. Returns (mapping, unmapped_keys).
    Unmapped keys = content the skeleton has no slot for.

    Preference order:
    1. Role match (when _V5_ENABLE_ROLE_MATCHING and the content key
       names a role and the skeleton has a slot with that role)
    2. First slot of matching kind (legacy behaviour)
    3. For keys in NEITHER explicit map: kind inferred from the value's
       shape (_v5_infer_kinds_from_value), first available slot wins
    """
    mapping: dict = {}
    used_ids: set[str] = set()
    unmapped: list[str] = []
    for key in content:
        target_kind = _CONTENT_KEY_TO_KIND.get(key)
        target_role = _CONTENT_KEY_TO_ROLE.get(key) if _V5_ENABLE_ROLE_MATCHING else None
        slot = None
        if target_role is not None:
            slot = _v5_first_slot_by_role(skeleton, target_role, used_ids)
        if slot is None and target_kind is not None:
            slot = _v5_first_slot_of_kind(skeleton, target_kind, used_ids)
        if slot is None and target_kind is None and target_role is None:
            for kind in _v5_infer_kinds_from_value(content[key]):
                slot = _v5_first_slot_of_kind(skeleton, kind, used_ids)
                if slot is not None:
                    break
        if slot is None:
            unmapped.append(key)
            continue
        mapping[key] = slot["id"]
        used_ids.add(slot["id"])
    return mapping, unmapped


def _v5_check_slot_fit(content_value: Any, slot: dict) -> tuple[bool, str, Any]:
    """Validates a content value against a slot. Returns (fits, reason,
    headroom). Headroom shape depends on kind.
    """
    constraints = slot.get("constraints") or {}
    kind = slot.get("kind")
    if kind in ("heading", "paragraph", "footer"):
        if isinstance(content_value, dict) and "value" in content_value:
            content_value = content_value["value"]
        return _v5_check_text_fit(str(content_value), constraints)
    if kind == "bullets":
        items = content_value if isinstance(content_value, list) else [content_value]
        return _v5_check_bullets_fit(items, constraints)
    if kind == "table":
        if isinstance(content_value, dict):
            return _v5_check_table_fit(content_value, constraints)
        return False, "table value must be a dict {rows, cols, has_header}", {}
    if kind == "chart":
        # Light validation: type whitelist + series/categories counts.
        if not isinstance(content_value, dict):
            return False, "chart value must be a dict {series, categories, type}", {}
        type_key = str(content_value.get("type") or "column").strip().lower()
        if type_key not in _V5_CHART_TYPE_MAP:
            return False, (f"chart type {type_key!r} not supported "
                           f"(use one of {sorted(_V5_CHART_TYPE_MAP)})"), {}
        n_series = content_value.get("n_series") or len(content_value.get("series", []) or [])
        n_cats = content_value.get("n_categories") or len(content_value.get("categories", []) or [])
        max_series = constraints.get("max_series", 99)
        max_cats = constraints.get("max_categories", 99)
        if n_series > max_series:
            return False, f"{n_series} series > max_series {max_series}", {}
        if n_cats > max_cats:
            return False, f"{n_cats} categories > max_categories {max_cats}", {}
        return True, "", {"series": max_series - n_series, "categories": max_cats - n_cats}
    if kind == "image":
        # Image content is typically just an asset_id string; full fit
        # check uses _v5_check_image_fit with the asset meta loaded.
        # Here we only validate the value's shape — content fit happens
        # in cmd_v5_check_asset_fit.
        # "placeholder" / {"placeholder": true, ...} are the documented
        # no-asset fallback (SKILL.md "Picking images") — valid as-is;
        # compose renders a labeled grey box for them.
        if content_value in ("placeholder", "asset_placeholder"):
            return True, "", {}
        if isinstance(content_value, dict) and content_value.get("placeholder"):
            return True, "", {}
        if isinstance(content_value, str) and content_value.startswith("asset_"):
            return True, "", {}
        if isinstance(content_value, dict) and "asset" in content_value:
            return True, "", {}
        return False, ("image value must be 'asset_<id>', {asset: ...}, "
                       "'placeholder', or {placeholder: true}"), {}
    return True, f"unknown kind {kind!r} — passing through", {}


def _v5_required_slots_filled(skeleton: dict, mapping: dict) -> list[str]:
    """Return ids of required slots that are NOT in the mapping (i.e.
    would be left empty by this content)."""
    missing = []
    mapped_ids = set(mapping.values())
    for s in skeleton.get("slots") or []:
        if (s.get("constraints") or {}).get("required") and s.get("id") not in mapped_ids:
            missing.append(s.get("id"))
    return missing


def _v5_headroom_summary(content_value: Any, slot: dict) -> str:
    """Human-friendly headroom string per kind. Used in match-skeletons."""
    kind = slot.get("kind")
    c = slot.get("constraints") or {}
    if kind in ("heading", "paragraph", "footer"):
        v = content_value["value"] if isinstance(content_value, dict) and "value" in content_value else content_value
        if c.get("max_chars"):
            return f"{c['max_chars'] - len(str(v))} chars to spare"
    if kind == "bullets":
        items = content_value if isinstance(content_value, list) else [content_value]
        if c.get("max_items"):
            return f"{c['max_items'] - len(items)} items to spare"
    if kind == "table" and isinstance(content_value, dict):
        if c.get("max_rows"):
            return f"{c['max_rows'] - content_value.get('rows', 0)} rows to spare"
    return ""


# --- CLI command implementations -------------------------------------------


def cmd_v5_list_themes(args: argparse.Namespace) -> None:
    out = []
    seen: set[str] = set()
    root = _v5_themes_dir()
    if root.exists():
        # Both layouts, mirroring _v5_load_theme: flat themes/<id>.yaml
        # (brief bundle) first, nested themes/<id>/theme.yaml fallback.
        flat = sorted(e for e in root.iterdir() if e.is_file() and e.suffix == ".yaml")
        nested = sorted(e for e in root.iterdir() if e.is_dir())
        for entry in flat + nested:
            if entry.is_file():
                t = _v5_load_yaml(entry)
                preview = None
            else:
                t = _v5_load_yaml(entry / "theme.yaml")
                preview = str(entry / "preview.png") if (entry / "preview.png").exists() else None
            if not t:
                continue
            tid = t.get("id") or entry.stem
            if tid in seen:
                continue
            seen.add(tid)
            out.append({
                "id": t.get("id"),
                "palette": t.get("palette", {}),
                "fonts": t.get("fonts", {}),
                "decoration_count": len(t.get("decorations") or []),
                "preview_path": preview,
            })
    out.sort(key=lambda t: str(t.get("id") or ""))
    json.dump({"themes": out}, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def cmd_v5_list_skeletons(args: argparse.Namespace) -> None:
    cats = set(args.category) if args.category else None
    has_slot = set(args.has_slot) if args.has_slot else None
    statuses = set(args.status) if args.status else {"pending", "done"}

    out = []
    for sk in _v5_iter_skeletons():
        if sk.get("status", "pending") not in statuses:
            continue
        sk_cats = set(sk.get("categories") or [])
        if cats and not (sk_cats & cats):
            continue
        sk_kinds = {s.get("kind") for s in (sk.get("slots") or [])}
        if has_slot and not (sk_kinds & has_slot):
            continue
        sk_dir = _v5_skeletons_dir() / sk.get("id", "")
        out.append({
            "id": sk.get("id"),
            "source_deck": sk.get("source_deck"),
            "source_slide_index": sk.get("source_slide_index"),
            "status": sk.get("status", "pending"),
            "categories": sk.get("categories") or [],
            "slot_count": len(sk.get("slots") or []),
            "slot_kinds": sorted(sk_kinds),
            "preview_path": str(sk_dir / "preview.png") if (sk_dir / "preview.png").exists() else None,
        })
    json.dump({"skeletons": out}, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def cmd_v5_get_skeleton(args: argparse.Namespace) -> None:
    sk = _v5_load_skeleton(args.id)
    if sk is None:
        raise SystemExit(f"skeleton not found: {args.id}")
    json.dump(sk, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def cmd_v5_get_theme(args: argparse.Namespace) -> None:
    t = _v5_load_theme(args.id)
    if t is None:
        raise SystemExit(f"theme not found: {args.id}")
    json.dump(t, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def cmd_v5_match_skeletons(args: argparse.Namespace) -> None:
    try:
        content = json.loads(args.content)
    except json.JSONDecodeError as e:
        raise SystemExit(f"--content must be valid JSON: {e}")
    if not isinstance(content, dict):
        raise SystemExit("--content must be a JSON object")

    filter_cats = set(args.category) if args.category else None
    filter_has_slot = set(args.has_slot) if args.has_slot else None

    candidates = []
    # Track tightest constraint per content key across all candidates
    # — used to drive the rephrase suggestion on zero-match.
    tightest_per_key: dict[str, dict] = {}
    # no_such_slot candidates — only reported if NO skeleton (post
    # category/has-slot filter) could map that key.
    unmappable_per_key: dict[str, dict] = {}
    mapped_keys: set[str] = set()
    # Skeletons skipped by the required-slot gate (capped at 5) —
    # reported on zero-match so the agent knows what to add.
    missing_required_issues: list[dict] = []

    for sk in _v5_iter_skeletons():
        if sk.get("status") == "rejected":
            continue
        sk_cats = set(sk.get("categories") or [])
        if filter_cats and not (sk_cats & filter_cats):
            continue
        sk_kinds = {s.get("kind") for s in (sk.get("slots") or [])}
        if filter_has_slot and not (sk_kinds & filter_has_slot):
            continue

        mapping, unmapped_keys = _v5_build_slot_mapping(content, sk)
        mapped_keys.update(mapping)
        if unmapped_keys:
            # Content key has no matching slot kind in this skeleton.
            for key in unmapped_keys:
                kind = _CONTENT_KEY_TO_KIND.get(key, key)
                if key not in unmappable_per_key:
                    unmappable_per_key[key] = {
                        "no_such_slot": True,
                        "suggested_action": f"no skeleton offers a '{kind}' slot for content key '{key}'",
                    }
            continue

        # Required-slot gate
        missing = _v5_required_slots_filled(sk, mapping)
        if missing:
            if len(missing_required_issues) < 5:
                slots_by_id = {s["id"]: s for s in (sk.get("slots") or [])}
                miss = [{"slot_id": m, "kind": slots_by_id.get(m, {}).get("kind")}
                        for m in missing]
                kinds = "/".join(sorted({str(m["kind"]) for m in miss}))
                ids = ", ".join(str(m["slot_id"]) for m in miss)
                missing_required_issues.append({
                    "skeleton_id": sk.get("id"),
                    "missing_required": miss,
                    "suggested_action": (f"add content for {kinds} slot(s) "
                                         f"'{ids}' or pick a different skeleton"),
                })
            continue

        # Fit each content piece
        all_fit = True
        slot_headroom: dict = {}
        slots_by_id = {s["id"]: s for s in (sk.get("slots") or [])}
        for key, slot_id in mapping.items():
            slot = slots_by_id[slot_id]
            fits, reason, headroom = _v5_check_slot_fit(content[key], slot)
            if not fits:
                all_fit = False
                # Track the tightest version of this constraint
                cur = tightest_per_key.get(key, {})
                c = slot.get("constraints") or {}
                if slot.get("kind") in ("heading", "paragraph", "footer"):
                    your_len = len(str(content[key] if not isinstance(content[key], dict) else content[key].get("value", "")))
                    constraint = c.get("max_chars", 0)
                    if "tightest_constraint" not in cur or constraint < cur["tightest_constraint"]:
                        cur.update({
                            "slot": key, "your_value": str(content[key])[:80],
                            "your_length": your_len, "tightest_constraint": constraint,
                            "suggested_action": f"rephrase to ≤{constraint} chars (drop {your_len - constraint})",
                        })
                        tightest_per_key[key] = cur
                elif slot.get("kind") == "bullets":
                    items = content[key] if isinstance(content[key], list) else [content[key]]
                    n = len(items)
                    constraint = c.get("max_items", 0)
                    if "tightest_constraint" not in cur or constraint < cur["tightest_constraint"]:
                        cur.update({
                            "slot": key, "your_count": n, "tightest_constraint": constraint,
                            "suggested_action": f"consolidate to ≤{constraint} items",
                        })
                        tightest_per_key[key] = cur
                elif slot.get("kind") == "table":
                    val = content[key] if isinstance(content[key], dict) else {}
                    constraint = c.get("max_rows", 0)
                    if "tightest_constraint" not in cur or constraint < cur["tightest_constraint"]:
                        cur.update({
                            "slot": key, "your_rows": val.get("rows", 0),
                            "your_cols": val.get("cols", 0),
                            "tightest_constraint": constraint, "reason": reason,
                            "suggested_action": (f"trim table to ≤{c.get('max_rows', '?')} rows "
                                                 f"× ≤{c.get('max_cols', '?')} cols"),
                        })
                        tightest_per_key[key] = cur
                elif slot.get("kind") == "chart":
                    val = content[key] if isinstance(content[key], dict) else {}
                    type_key = str(val.get("type") or "column").strip().lower()
                    if type_key not in _V5_CHART_TYPE_MAP:
                        action = (f"use a supported chart type "
                                  f"(one of {sorted(_V5_CHART_TYPE_MAP)})")
                    else:
                        action = (f"reduce chart data to ≤{c.get('max_series', '?')} series "
                                  f"and ≤{c.get('max_categories', '?')} categories")
                    constraint = c.get("max_categories", 0)
                    if "tightest_constraint" not in cur or constraint < cur["tightest_constraint"]:
                        cur.update({
                            "slot": key, "tightest_constraint": constraint,
                            "reason": reason, "suggested_action": action,
                        })
                        tightest_per_key[key] = cur
                elif slot.get("kind") == "image":
                    if "suggested_action" not in cur:
                        cur.update({
                            "slot": key, "reason": reason,
                            "suggested_action": ("use 'asset_<id>' from find-asset, or "
                                                 "'placeholder' when no asset fits"),
                        })
                        tightest_per_key[key] = cur
                break  # one issue per skeleton is enough
            slot_headroom[slot_id] = _v5_headroom_summary(content[key], slot)

        if not all_fit:
            continue

        # Compute fit_score
        tightness_scores = []
        for key, slot_id in mapping.items():
            slot = slots_by_id[slot_id]
            c = slot.get("constraints") or {}
            value = content[key]
            if slot.get("kind") in ("heading", "paragraph", "footer"):
                v = value["value"] if isinstance(value, dict) and "value" in value else value
                m = c.get("max_chars") or len(str(v))
                if m > 0:
                    tightness_scores.append(min(1.0, len(str(v)) / m))
            elif slot.get("kind") == "bullets":
                items = value if isinstance(value, list) else [value]
                m = c.get("max_items") or len(items)
                if m > 0:
                    tightness_scores.append(min(1.0, len(items) / m))
        tightness = sum(tightness_scores) / len(tightness_scores) if tightness_scores else 0.5

        cat_bonus = 0.10 if filter_cats and (sk_cats & filter_cats) else 0
        extra_slots = max(0, len(sk.get("slots") or []) - len(mapping))
        extra_bonus = min(0.20, 0.05 * extra_slots)

        fit_score = min(1.0, tightness * 0.70 + cat_bonus + extra_bonus)

        candidates.append({
            "skeleton_id": sk["id"],
            "categories": list(sk_cats),
            "fit_score": round(fit_score, 3),
            "slot_mapping": mapping,
            "headroom": slot_headroom,
        })

    candidates.sort(key=lambda c: c["fit_score"], reverse=True)

    if candidates:
        result = {"matches": candidates, "issues": []}
    else:
        issues = list(tightest_per_key.values())
        # no_such_slot is only honest if NO skeleton mapped that key.
        issues.extend(v for k, v in unmappable_per_key.items()
                      if k not in mapped_keys)
        issues.extend(missing_required_issues)
        result = {"matches": [], "issues": issues}

    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def _v5_validate_plan_data(plan: list) -> tuple[list[dict], list[dict]]:
    """Constraint-check a parsed plan. Returns (errors, warnings).
    Shared by validate-plan and compose-v5 (which runs it pre-build)."""
    errors: list[dict] = []
    warnings: list[dict] = []

    for i, entry in enumerate(plan):
        if not isinstance(entry, dict):
            errors.append({
                "slide_index": i, "slot_id": None,
                "violation": "malformed_entry",
                "message": (f"plan entry {i} must be an object with "
                            f"skeleton_id + slots, got {type(entry).__name__}"),
            })
            continue
        sk_id = entry.get("skeleton_id")
        sk = _v5_load_skeleton(sk_id) if sk_id else None
        if sk is None:
            errors.append({
                "slide_index": i, "slot_id": None,
                "violation": "skeleton_not_found",
                "message": f"no skeleton with id {sk_id!r}",
            })
            continue
        slots_by_id = {s["id"]: s for s in (sk.get("slots") or [])}
        filled = entry.get("slots") or {}
        if not isinstance(filled, dict):
            errors.append({
                "slide_index": i, "slot_id": None,
                "violation": "malformed_entry",
                "message": (f"plan entry {i} 'slots' must be an object, "
                            f"got {type(filled).__name__}"),
            })
            continue

        # Required slots
        for slot in sk.get("slots") or []:
            if (slot.get("constraints") or {}).get("required") and slot["id"] not in filled:
                errors.append({
                    "slide_index": i, "slot_id": slot["id"],
                    "violation": "required_unfilled",
                    "message": f"required slot {slot['id']!r} not in plan",
                })

        # Constraint checks on filled slots
        for slot_id, value in filled.items():
            slot = slots_by_id.get(slot_id)
            if slot is None:
                errors.append({
                    "slide_index": i, "slot_id": slot_id,
                    "violation": "unknown_slot",
                    "message": f"slot {slot_id!r} not in skeleton {sk_id!r}",
                })
                continue
            is_overflow_shrink = isinstance(value, dict) and value.get("overflow") == "shrink"
            inner = value.get("value", value) if isinstance(value, dict) else value
            fits, reason, _ = _v5_check_slot_fit(inner, slot)
            if not fits:
                if is_overflow_shrink:
                    warnings.append({
                        "slide_index": i, "slot_id": slot_id,
                        "overflow_kind": "shrink", "message": reason,
                    })
                else:
                    errors.append({
                        "slide_index": i, "slot_id": slot_id,
                        "violation": "constraint", "message": reason,
                    })
                continue

            # Asset existence + aspect for image slots referencing a
            # real asset id (not the placeholder sentinel). Catches
            # hallucinated ids before they reach the user's compose.
            if slot.get("kind") == "image":
                asset_id = None
                if isinstance(inner, str) and inner.startswith("asset_") and inner != "asset_placeholder":
                    asset_id = inner
                elif isinstance(inner, dict) and not inner.get("placeholder"):
                    asset_id = inner.get("asset") or inner.get("asset_id")
                if asset_id:
                    meta = _v5_load_asset_meta(asset_id)
                    if meta is None:
                        errors.append({
                            "slide_index": i, "slot_id": slot_id,
                            "violation": "unknown_asset",
                            "message": (f"asset {asset_id!r} not in the asset "
                                        f"library — pick an id from find-asset, "
                                        f"or use 'placeholder'"),
                        })
                    else:
                        a_fits, a_reason, _t = _v5_check_image_fit(
                            meta, slot.get("constraints") or {})
                        if not a_fits:
                            warnings.append({
                                "slide_index": i, "slot_id": slot_id,
                                "violation": "aspect_mismatch",
                                "message": a_reason,
                            })

    return errors, warnings


def cmd_v5_validate_plan(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan)
    if not plan_path.exists():
        raise SystemExit(f"plan not found: {plan_path}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"plan is not valid JSON: {e}")
    if not isinstance(plan, list):
        raise SystemExit("plan must be a JSON array")

    errors, warnings = _v5_validate_plan_data(plan)

    result = {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def cmd_v5_check_asset_fit(args: argparse.Namespace) -> None:
    sk = _v5_load_skeleton(args.skeleton_id)
    if sk is None:
        raise SystemExit(f"skeleton not found: {args.skeleton_id}")
    slot = next((s for s in (sk.get("slots") or []) if s.get("id") == args.slot_id), None)
    if slot is None:
        raise SystemExit(f"slot {args.slot_id!r} not in {args.skeleton_id}")
    if slot.get("kind") != "image":
        json.dump({
            "fits": False, "will_resize_to": None, "will_crop": False,
            "reason": f"slot is kind={slot.get('kind')!r}, not image",
            "suggestion": "pick an image slot or change the slot kind",
        }, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return
    meta = _v5_load_asset_meta(args.asset_id)
    if meta is None:
        json.dump({
            "fits": False, "will_resize_to": None, "will_crop": False,
            "reason": f"asset not found: {args.asset_id}",
            "suggestion": "pick an id from find-asset, or use \"placeholder\"",
        }, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return
    fits, reason, transform = _v5_check_image_fit(meta, slot.get("constraints") or {})
    result = {
        "fits": fits,
        "will_resize_to": None,
        "will_crop": transform.get("will_crop", False),
        "reason": reason or None,
        "suggestion": ("would crop to slot aspect" if transform.get("will_crop") else None) if not fits else None,
    }
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def cmd_v5_find_asset(args: argparse.Namespace) -> None:
    """Deterministic shortlist filter over the asset library.

    Filter dimensions:
      --kind   required; matches the asset's ``kind`` exactly
      --tags   optional, repeatable; an asset matches if it carries
               every requested tag (AND, not OR)

    `description` is included in each match for picking the final 1-of-N
    by topic fit, but is never a filter input. `width`/`height`/`aspect`
    ride along so the agent can call check-asset-fit on the shortlist
    without a second round-trip.

    Two runs against the same library + same query produce the same
    shortlist in the same order (sorted by id, ascending). Idempotent
    by construction.

    If the filter is too tight to return any candidates, ``suggestion``
    points to the broadening step.
    """
    index = load_index()
    assets = index.get("assets", []) or []

    pool = [a for a in assets if str(a.get("kind", "")) == args.kind]
    total_kind = len(pool)

    wanted_tags = list(args.tags or [])
    if wanted_tags:
        pool = [
            a for a in pool
            if all(t in (a.get("tags") or []) for t in wanted_tags)
        ]

    pool.sort(key=lambda a: str(a.get("id", "")))
    limit = max(1, int(args.limit or 5))
    shortlist = pool[:limit]

    suggestion: str | None = None
    if not shortlist:
        if wanted_tags:
            suggestion = (
                f"no match — drop --tags and retry with --kind {args.kind} "
                f"only. If still empty, either stage a new asset "
                f'(POST /api/asset/add) or pass "placeholder" as the '
                f"asset_id to render a labeled grey box for manual "
                f"replacement post-build."
            )
        else:
            suggestion = (
                f"no assets of kind={args.kind!r} in the library. "
                f'Stage one via /api/asset/add or use "placeholder".'
            )

    out = {
        "query": {
            "kind": args.kind,
            "tags": wanted_tags,
        },
        "matches": [
            {
                "id": a.get("id"),
                "kind": a.get("kind"),
                "tags": list(a.get("tags") or []),
                "description": a.get("description", ""),
                "width": int(a.get("width") or 0),
                "height": int(a.get("height") or 0),
                "aspect": float(a.get("aspect") or 0.0),
                "colors_hex": list(a.get("colors_hex") or []),
            }
            for a in shortlist
        ],
        "count": len(shortlist),
        "total_of_kind": total_kind,
        "tag_vocab": list(index.get("tag_vocab") or []),
        "suggestion": suggestion,
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def cmd_v5_compose(args: argparse.Namespace) -> None:
    """Build a deck from a v5 plan on a chosen host theme's master.

    Opens themes/<theme_id>/master.pptx as host, strips its slides,
    then for each plan entry creates a new blank slide and places
    primitives per the skeleton's slot inventory.

    Plan shape (same as validate-plan):
      [{"skeleton_id": "...", "slots": {"slot_id": value, ...}}, ...]

    Slot value shapes:
      string → text content
      list[string] → bullets
      dict {value, overflow: "shrink"} → text with autofit
      dict {rows, cols, has_header, data: [[...]]} → table
      dict {type, series, categories} → chart (deferred — emits warning)
      "asset_<id>" or dict {asset: "..."} → image

    Writes a JSON result to stdout with output path + warnings,
    plus a <out>.warnings.json sidecar with the same warnings for
    the user to triage overflow:shrink events after the deck opens.
    """
    from pptx import Presentation
    from pptx.util import Emu, Pt

    plan_path = Path(args.plan)
    if not plan_path.exists():
        raise SystemExit(f"plan not found: {plan_path}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"plan is not valid JSON: {e}")
    if not isinstance(plan, list) or not plan:
        raise SystemExit("plan must be a non-empty JSON array")

    # Pre-flight: same checks as validate-plan. Hard errors block the
    # build unless --force; validation warnings always ride along into
    # the compose warnings + sidecar.
    val_errors, val_warnings = _v5_validate_plan_data(plan)
    force = bool(getattr(args, "force", False))
    if val_errors and not force:
        json.dump({
            "ok": False, "errors": val_errors, "warnings": val_warnings,
            "message": "plan failed validation — fix the errors above, or re-run with --force",
        }, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        raise SystemExit(1)

    theme = _v5_load_theme(args.theme)
    if theme is None:
        raise SystemExit(f"theme not found: {args.theme}")
    master_path = _v5_themes_dir() / args.theme / theme.get("master_pptx", "master.pptx")
    if not master_path.exists():
        raise SystemExit(
            f"theme master.pptx missing: {master_path}\n"
            f"This looks like a text-only brief bundle (mode A) — theme "
            f"binaries are not shipped. Your job is to emit a validated "
            f"plan.json for the user to compose locally, not to run "
            f"compose-v5 yourself (see SKILL.md, 'What's in the bundle')."
        )

    out_path = Path(args.out)
    warnings: list[dict] = []
    warnings.extend(val_warnings)
    if val_errors:
        # Only reachable with --force — keep the errors visible in the
        # warnings list + sidecar so the override leaves a trace.
        warnings.extend(val_errors)

    with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as tmp:
        shutil.copyfile(master_path, tmp.name)
        host_path = Path(tmp.name)

    try:
        prs = Presentation(str(host_path))
        slide_w = prs.slide_width or 9144000
        slide_h = prs.slide_height or 6858000

        # Strip the source's existing slides — we build fresh.
        _v5_drop_all_slides(prs)

        # Pick a blank-ish layout to host each new slide.
        blank_layout = _v5_pick_blank_layout(prs)

        built = 0
        for i, entry in enumerate(plan):
            if not isinstance(entry, dict):
                warnings.append({"slide_index": i, "violation": "malformed_entry",
                                 "message": (f"plan entry {i} is "
                                             f"{type(entry).__name__}, expected "
                                             f"an object — skipped")})
                continue
            sk_id = entry.get("skeleton_id")
            sk = _v5_load_skeleton(sk_id) if sk_id else None
            if sk is None:
                warnings.append({"slide_index": i, "violation": "skeleton_not_found",
                                 "message": f"no skeleton {sk_id!r}"})
                continue

            slide = prs.slides.add_slide(blank_layout)
            built += 1
            slots_by_id = {s["id"]: s for s in (sk.get("slots") or [])}
            filled = entry.get("slots") or {}
            if not isinstance(filled, dict):
                warnings.append({"slide_index": i, "violation": "malformed_entry",
                                 "message": (f"plan entry {i} 'slots' is "
                                             f"{type(filled).__name__}, expected "
                                             f"an object — slide left empty")})
                filled = {}

            # Apply background_image (B4-render) if the skeleton has
            # one. Paint full-bleed FIRST so subsequent slot shapes
            # stack on top via python-pptx's natural z-order. Fail-soft:
            # missing file or any add_picture error → warn and proceed
            # without the underlay (deck still renders, just without
            # the structural illustration baked in).
            bg_rel = sk.get("background_image")
            if bg_rel:
                bg_path = _v5_skeletons_dir() / sk_id / bg_rel
                if not bg_path.exists():
                    warnings.append({
                        "slide_index": i, "slot_id": "_background",
                        "violation": "background_missing",
                        "message": f"background_image set to {bg_rel!r} "
                                   f"but file not in bundle; slide built without underlay",
                    })
                else:
                    try:
                        slide.shapes.add_picture(
                            str(bg_path), 0, 0,
                            width=slide_w, height=slide_h,
                        )
                    except Exception as e:
                        warnings.append({
                            "slide_index": i, "slot_id": "_background",
                            "violation": "background_place_failed",
                            "message": f"{type(e).__name__}: {e}; "
                                       f"slide built without underlay",
                        })

            for slot_id, value in filled.items():
                slot = slots_by_id.get(slot_id)
                if slot is None:
                    warnings.append({
                        "slide_index": i, "slot_id": slot_id,
                        "violation": "unknown_slot",
                        "message": f"slot {slot_id!r} not in skeleton {sk_id!r}",
                    })
                    continue
                ws = _v5_place_slot(slide, slot, value, slide_w, slide_h, theme)
                for w in ws:
                    w.setdefault("slide_index", i)
                    w.setdefault("slot_id", slot_id)
                warnings.extend(ws)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        prs.save(str(out_path))

        # Sidecar for human review of overflow/warning events.
        if warnings:
            sidecar = out_path.with_suffix(out_path.suffix + ".warnings.json")
            sidecar.write_text(
                json.dumps({"warnings": warnings}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
    finally:
        try:
            host_path.unlink()
        except OSError:
            pass

    result = {
        "output": str(out_path),
        "slides": built,
        "warnings": warnings,
        "warnings_sidecar": str(out_path.with_suffix(out_path.suffix + ".warnings.json")) if warnings else None,
    }
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def _v5_drop_all_slides(prs) -> None:
    """Remove every slide from the host master so we can add fresh
    ones for each plan entry. Mirrors the pattern in v4's
    _drop_first_slide but applied repeatedly.
    """
    sldIdLst = prs.slides._sldIdLst
    rId_to_drop = []
    for sldId in list(sldIdLst):
        rId_to_drop.append(sldId.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"))
        sldIdLst.remove(sldId)
    for rId in rId_to_drop:
        if rId:
            try:
                prs.part.drop_rel(rId)
            except KeyError:
                pass


def _v5_pick_blank_layout(prs):
    """Pick the simplest available layout (fewest placeholders).
    Mirrors v4's "fewest placeholders" heuristic.
    """
    layouts = list(prs.slide_layouts)
    if not layouts:
        raise SystemExit("host master has no slide layouts")
    return min(layouts, key=lambda lo: len(list(lo.placeholders)))


def _v5_emu_geometry(slot: dict, slide_w: int, slide_h: int) -> tuple[int, int, int, int]:
    """Fractional → EMU. Returns (left, top, width, height) in EMU."""
    g = slot.get("geometry") or {}
    return (
        int((g.get("x", 0)) * slide_w),
        int((g.get("y", 0)) * slide_h),
        int((g.get("w", 0)) * slide_w),
        int((g.get("h", 0)) * slide_h),
    )


def _v5_resolve_font_name(slot: dict, theme: dict) -> str | None:
    """Resolve a slot's style.font_role against the host theme."""
    style = slot.get("style") or {}
    role = style.get("font_role")
    fonts = theme.get("fonts") or {}
    if role == "major":
        return fonts.get("major")
    if role == "minor":
        return fonts.get("minor")
    if role == "explicit":
        return style.get("typeface")
    return None


def _v5_resolve_color(slot: dict, theme: dict):
    """Resolve a slot's style.color_role or .color → RGBColor or None."""
    from pptx.dml.color import RGBColor
    style = slot.get("style") or {}
    role = style.get("color_role")
    palette = theme.get("palette") or {}
    hex_val = None
    if role and role in palette:
        hex_val = palette[role]
    elif style.get("color"):
        hex_val = style["color"]
    if not hex_val:
        return None
    try:
        return RGBColor.from_string(hex_val.lstrip("#"))
    except Exception:
        return None


def _v5_place_slot(slide, slot: dict, value, slide_w: int, slide_h: int, theme: dict) -> list[dict]:
    """Dispatch to per-kind placers."""
    kind = slot.get("kind")
    warnings: list[dict] = []
    overflow = None
    if isinstance(value, dict) and "overflow" in value:
        overflow = value.get("overflow")
        value = value.get("value", value)
    try:
        if kind in ("heading", "paragraph", "footer"):
            warnings.extend(_v5_place_text(slide, slot, value, slide_w, slide_h, theme, overflow))
        elif kind == "bullets":
            items = value if isinstance(value, list) else [value]
            warnings.extend(_v5_place_bullets(slide, slot, items, slide_w, slide_h, theme, overflow))
        elif kind == "image":
            warnings.extend(_v5_place_image(slide, slot, value, slide_w, slide_h, theme))
        elif kind == "table":
            warnings.extend(_v5_place_table(slide, slot, value, slide_w, slide_h, theme))
        elif kind == "chart":
            warnings.extend(_v5_place_chart(slide, slot, value, slide_w, slide_h, theme))
        else:
            warnings.append({"violation": "unknown_kind",
                             "message": f"unknown slot kind {kind!r}"})
    except Exception as e:
        warnings.append({"violation": "place_failed",
                         "message": f"{type(e).__name__}: {e}"})
    return warnings


def _v5_place_text(slide, slot: dict, value, slide_w, slide_h, theme, overflow) -> list[dict]:
    from pptx.util import Pt
    from pptx.enum.text import MSO_ANCHOR
    warnings: list[dict] = []
    left, top, w, h = _v5_emu_geometry(slot, slide_w, slide_h)
    tb = slide.shapes.add_textbox(left, top, w, h)
    tf = tb.text_frame
    tf.word_wrap = True

    text = str(value)
    style = slot.get("style") or {}
    configured_pt = float(style.get("size_pt") or 18.0)

    # Auto-shrink policy (text + bullet slots):
    #   default        — autofit IF text estimated to fit at floor
    #                    (max(8pt, 70% of configured); else leave
    #                    overflowing AND warn so user sees + fixes
    #   "shrink"       — force autofit even when it'd go below floor
    #                    (explicit opt-in to potential illegibility)
    #   "none"         — no autofit, raw overflow (pre-#18 behavior)
    enable_autofit = False
    if overflow == "shrink":
        enable_autofit = True
        # Surface the promised sidecar warning when the text actually
        # exceeds the slot constraint (SKILL.md escape hatch).
        c = slot.get("constraints") or {}
        s_fits, s_reason, _hr = _v5_check_text_fit(text, c)
        if not s_fits:
            warnings.append({
                "violation": "overflow_shrink",
                "slot_id": slot.get("id"),
                "constraint": c.get("max_chars"),
                "actual_chars": len(text),
                "message": (f"slot {slot.get('id')!r}: {s_reason}; "
                            f"font auto-shrunk to fit (overflow:'shrink') — "
                            f"review legibility"),
            })
    elif overflow == "none":
        enable_autofit = False
    else:
        # Default: only enable if text fits at floor.
        floor_pt = max(8.0, 0.7 * configured_pt)
        fits, est = _v5_estimate_fits(text, w, h, floor_pt)
        if fits:
            enable_autofit = True
        else:
            warnings.append({
                "violation": "text_overflow",
                "slot_id": slot.get("id"),
                "message": (
                    f"text in slot {slot.get('id')!r} won't fit at floor "
                    f"({floor_pt:.0f}pt): {len(text)} chars vs an estimated "
                    f"{est} fitting at floor. Rendered with overflow — "
                    f"shorten the text, or pass overflow:'shrink' to force "
                    f"unbounded auto-shrink."
                ),
            })

    try:
        from pptx.enum.text import MSO_AUTO_SIZE
        # Explicit set in both cases: enable_autofit → TEXT_TO_FIT_SHAPE
        # (font shrinks to fit). Otherwise → NONE (text overflows the
        # visible box but the shape stays at slot bounds, so it doesn't
        # push into adjacent slots). Default for add_textbox() is
        # SHAPE_TO_FIT_TEXT which would grow the shape — exactly what
        # we don't want when the user has a tight layout.
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE if enable_autofit else MSO_AUTO_SIZE.NONE
    except Exception:
        pass

    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = text
    if style.get("size_pt"):
        run.font.size = Pt(style["size_pt"])
    if style.get("bold") is not None:
        run.font.bold = style["bold"]
    if style.get("italic") is not None:
        run.font.italic = style["italic"]
    font_name = _v5_resolve_font_name(slot, theme)
    if font_name:
        run.font.name = font_name
    color = _v5_resolve_color(slot, theme)
    if color is not None:
        run.font.color.rgb = color
    return warnings


def _v5_estimate_fits(text: str, w_emu: int, h_emu: int, font_pt: float) -> tuple[bool, int]:
    """Rough estimate: does ``text`` fit in a ``w_emu × h_emu`` box at
    ``font_pt`` font size? Returns (fits, est_chars_at_this_size).

    Uses a sans-serif approximation: average char width ≈ 0.55 × font
    size in points, line height ≈ 1.2 × font size. EMU ≈ 12700 per pt.
    Conservative for short labels; less accurate for paragraphs with
    very narrow / wide characters. Good enough to catch egregious
    overflow ("200 chars in a 30-char slot").
    """
    if font_pt <= 0 or w_emu <= 0 or h_emu <= 0:
        return True, len(text)
    pt_to_emu = 12700.0
    char_w = font_pt * 0.55 * pt_to_emu
    line_h = font_pt * 1.2 * pt_to_emu
    chars_per_line = max(1, int(w_emu / char_w))
    lines_available = max(1, int(h_emu / line_h))
    capacity = chars_per_line * lines_available
    return len(text) <= capacity, capacity


_V5_BULLET_GLYPH_RE = re.compile(r"^[•\-–*]\s+")


def _v5_place_bullets(slide, slot: dict, items: list, slide_w, slide_h, theme, overflow) -> list[dict]:
    from pptx.util import Pt
    warnings: list[dict] = []
    left, top, w, h = _v5_emu_geometry(slot, slide_w, slide_h)
    tb = slide.shapes.add_textbox(left, top, w, h)
    tf = tb.text_frame
    tf.word_wrap = True

    # Strip a single leading bullet glyph from agent-provided items —
    # we prefix "• " ourselves, so "• item" would double-glyph.
    items = [_V5_BULLET_GLYPH_RE.sub("", str(it), count=1) for it in items]

    style = slot.get("style") or {}
    configured_pt = float(style.get("size_pt") or 14.0)
    # Estimate per the same heuristic as _v5_place_text. We count the
    # total chars across items + the bullet glyph + newlines as a rough
    # proxy for visual length.
    total_text = "\n".join(f"• {it}" for it in items)

    enable_autofit = False
    if overflow == "shrink":
        enable_autofit = True
        # Surface the promised sidecar warning when the items actually
        # exceed the slot constraint (SKILL.md escape hatch).
        c = slot.get("constraints") or {}
        s_fits, s_reason, _hr = _v5_check_bullets_fit(items, c)
        if not s_fits:
            warnings.append({
                "violation": "overflow_shrink",
                "slot_id": slot.get("id"),
                "constraint": {"max_items": c.get("max_items"),
                               "max_chars_per_item": c.get("max_chars_per_item")},
                "actual_chars": len(total_text),
                "message": (f"slot {slot.get('id')!r}: {s_reason}; "
                            f"font auto-shrunk to fit (overflow:'shrink') — "
                            f"review legibility"),
            })
    elif overflow == "none":
        enable_autofit = False
    else:
        floor_pt = max(8.0, 0.7 * configured_pt)
        fits, est = _v5_estimate_fits(total_text, w, h, floor_pt)
        if fits:
            enable_autofit = True
        else:
            warnings.append({
                "violation": "bullets_overflow",
                "slot_id": slot.get("id"),
                "message": (
                    f"bullets in slot {slot.get('id')!r} won't fit at floor "
                    f"({floor_pt:.0f}pt): {len(total_text)} chars across "
                    f"{len(items)} items vs an estimated {est} fitting at "
                    f"floor. Rendered with overflow — trim items, or pass "
                    f"overflow:'shrink' to force unbounded auto-shrink."
                ),
            })

    try:
        from pptx.enum.text import MSO_AUTO_SIZE
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE if enable_autofit else MSO_AUTO_SIZE.NONE
    except Exception:
        pass

    font_name = _v5_resolve_font_name(slot, theme)
    color = _v5_resolve_color(slot, theme)

    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = p.add_run()
        run.text = f"• {item}"
        if style.get("size_pt"):
            run.font.size = Pt(style["size_pt"])
        if style.get("bold") is not None:
            run.font.bold = style["bold"]
        if font_name:
            run.font.name = font_name
        if color is not None:
            run.font.color.rgb = color
    return warnings


def _v5_draw_placeholder_box(slide, left: int, top: int, w: int, h: int, label: str) -> None:
    """Draw a labeled grey rectangle in place of a missing image asset.

    Used by the "placeholder" sentinel asset_id — agents emit this when
    find-asset returns empty for a required slot. The box is dashed +
    light grey so it reads as "fix me" in the final deck.
    """
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.dml.color import RGBColor
    from pptx.util import Pt
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, w, h)
    fill = shape.fill
    fill.solid()
    fill.fore_color.rgb = RGBColor(0xEE, 0xEE, 0xEE)
    line = shape.line
    line.color.rgb = RGBColor(0xAA, 0xAA, 0xAA)
    try:
        from pptx.enum.dml import MSO_LINE_DASH_STYLE
        line.dash_style = MSO_LINE_DASH_STYLE.DASH
    except Exception:
        pass
    tf = shape.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = label
    run.font.size = Pt(12)
    run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    run.font.italic = True


def _v5_place_image(slide, slot: dict, value, slide_w, slide_h, theme) -> list[dict]:
    warnings: list[dict] = []
    left, top, w, h = _v5_emu_geometry(slot, slide_w, slide_h)
    # Resolve asset id from value
    asset_id = None
    placeholder_label: str | None = None
    if isinstance(value, str):
        asset_id = value
    elif isinstance(value, dict):
        if value.get("placeholder"):
            placeholder_label = str(value.get("label") or "")
            asset_id = "placeholder"
        else:
            asset_id = value.get("asset") or value.get("asset_id")
    # Placeholder sentinel: emit a labeled grey box instead of a binary.
    # The agent uses this when find-asset returns empty for a required
    # slot and no online sourcing was possible. Build emits a warning so
    # the user sees in the sidecar which slots still need a real asset.
    if asset_id in ("placeholder", "asset_placeholder"):
        slot_id = slot.get("id") or "?"
        label = placeholder_label or f"image needed: {slot_id}"
        _v5_draw_placeholder_box(slide, left, top, w, h, label)
        warnings.append({
            "violation": "image_placeholder",
            "slot_id": slot_id,
            "message": f"placeholder rendered for slot {slot_id!r} — replace with a real asset",
        })
        return warnings
    if not asset_id:
        warnings.append({"violation": "no_asset", "message": "image value missing asset id"})
        return warnings
    # Find the asset binary
    assets_dir = _v5_assets_dir()
    if not assets_dir.exists():
        warnings.append({"violation": "no_assets_dir", "message": f"assets dir missing: {assets_dir}"})
        return warnings
    bin_path = None
    for cand in assets_dir.glob(f"{asset_id}.*"):
        if cand.suffix == ".yaml":
            continue
        bin_path = cand
        break
    if bin_path is None:
        warnings.append({"violation": "asset_not_found", "message": f"asset {asset_id} binary missing"})
        return warnings

    # Aspect-aware placement per the slot's auto_fit policy. Default
    # "cover" matches the agent contract — center-crop preserving
    # aspect to fill the slot. "contain" letterboxes; "stretch" is
    # the old distorting behaviour, kept for opt-in compatibility.
    fit = (slot.get("constraints") or {}).get("auto_fit") or "cover"
    asset_w, asset_h = _v5_image_dimensions(bin_path)
    if asset_w <= 0 or asset_h <= 0 or fit == "stretch":
        # Unknown dims or explicit stretch → fall back to direct fit
        # (matches the pre-aspect behaviour; cheaper than refusing).
        slide.shapes.add_picture(str(bin_path), left, top, w, h)
        if fit != "stretch" and (asset_w <= 0 or asset_h <= 0):
            warnings.append({
                "violation": "asset_dims_unknown",
                "message": f"could not read dimensions of {bin_path.name}; placed stretched",
            })
        return warnings

    asset_aspect = asset_w / asset_h
    slot_aspect = w / h if h > 0 else 1.0

    if fit == "contain":
        # Letterbox: shrink to fit inside slot, leave bands.
        if asset_aspect > slot_aspect:
            placed_w = w
            placed_h = int(w / asset_aspect)
        else:
            placed_h = h
            placed_w = int(h * asset_aspect)
        placed_left = left + (w - placed_w) // 2
        placed_top = top + (h - placed_h) // 2
        slide.shapes.add_picture(str(bin_path), placed_left, placed_top, placed_w, placed_h)
        return warnings

    # Default "cover": picture shape stays at slot bounds; the SOURCE
    # image gets cropped on the over-sized axis so the visible portion
    # matches the slot's aspect. Previously the shape was inflated past
    # the slot bounds and then crop hid the overflow — that worked
    # visually for the cropped pixels but the shape's bounding box still
    # extended into adjacent slots (and into the slide margin / off-slide
    # for very wide assets), which broke layouts that put another slot
    # right next to an image (e.g. standard_templates_04/09: bullets on
    # the left, hero image on the right).
    #
    # python-pptx crop_* values are fractions of the SOURCE image to hide
    # from each edge. By cropping the source so its visible region has
    # the slot's aspect, the picture shape at slot dimensions renders the
    # cropped region scaled to fill — no shape inflation needed.
    pic = slide.shapes.add_picture(str(bin_path), left, top, w, h)
    if asset_aspect > slot_aspect:
        # Asset is wider than slot → crop equal slivers off the sides.
        # Visible source width = slot.w / (slot.h / asset.h) → ratio
        # cropped per side = (1 - slot_aspect/asset_aspect) / 2.
        crop = (1 - slot_aspect / asset_aspect) / 2
        pic.crop_left = crop
        pic.crop_right = crop
    elif asset_aspect < slot_aspect:
        # Asset is taller than slot → crop equal slivers off top + bottom.
        crop = (1 - asset_aspect / slot_aspect) / 2
        pic.crop_top = crop
        pic.crop_bottom = crop
    # asset_aspect == slot_aspect → no cropping needed, shape fits exactly.
    return warnings


def _v5_image_dimensions(path: Path) -> tuple[int, int]:
    """Return (width, height) of a raster image in pixels, or (0, 0)
    on any failure. PIL is the existing dependency for v4 dominant-
    colour extraction, so it's already available.
    """
    try:
        from PIL import Image
        with Image.open(path) as img:
            return img.size
    except Exception:
        return 0, 0


def _v5_place_table(slide, slot: dict, value, slide_w, slide_h, theme) -> list[dict]:
    from pptx.util import Pt
    warnings: list[dict] = []
    left, top, w, h = _v5_emu_geometry(slot, slide_w, slide_h)
    if not isinstance(value, dict):
        warnings.append({"violation": "bad_table", "message": "table value must be dict"})
        return warnings
    data = value.get("data") or []
    rows = value.get("rows") or len(data)
    cols = value.get("cols") or (len(data[0]) if data else 0)
    if rows < 1 or cols < 1:
        warnings.append({"violation": "empty_table", "message": "table needs rows/cols"})
        return warnings
    tbl_shape = slide.shapes.add_table(rows, cols, left, top, w, h)
    tbl = tbl_shape.table
    for r in range(min(rows, len(data))):
        row_data = data[r]
        for c in range(min(cols, len(row_data))):
            cell = tbl.cell(r, c)
            cell.text = str(row_data[c])
    return warnings


# Chart type strings → python-pptx XL_CHART_TYPE attribute names. Kept
# as a small whitelist; unknown types emit a warning and skip rather
# than crash. Scatter charts use XyChartData (different data shape) so
# they're not included here — add when there's a real need.
_V5_CHART_TYPE_MAP = {
    "bar": "BAR_CLUSTERED",
    "bar_clustered": "BAR_CLUSTERED",
    "bar_stacked": "BAR_STACKED",
    "column": "COLUMN_CLUSTERED",
    "column_clustered": "COLUMN_CLUSTERED",
    "column_stacked": "COLUMN_STACKED",
    "line": "LINE",
    "line_markers": "LINE_MARKERS",
    "pie": "PIE",
    "doughnut": "DOUGHNUT",
    "area": "AREA",
    "area_stacked": "AREA_STACKED",
}


def _v5_place_chart(slide, slot: dict, value, slide_w, slide_h, theme) -> list[dict]:
    """Build a category chart from primitives via python-pptx's
    add_chart. Replaces the old "chart_not_implemented" warning.

    Expected value shape:
      {
        "type": "bar|column|line|pie|doughnut|area" (+ variants),
        "categories": ["Q1", "Q2", "Q3"],
        "series": [{"name": "Revenue", "values": [10, 20, 30]}, ...]
      }

    Fail-soft: malformed value, unknown type, or any python-pptx
    exception → append a warning and leave the slot empty. Matches
    the previous fail-soft behaviour so a broken chart spec never
    crashes the whole compose run.
    """
    warnings: list[dict] = []
    left, top, w, h = _v5_emu_geometry(slot, slide_w, slide_h)

    if not isinstance(value, dict):
        warnings.append({
            "violation": "bad_chart",
            "message": "chart value must be a dict {type, categories, series}",
        })
        return warnings

    type_key = str(value.get("type") or "column").strip().lower()
    mapped = _V5_CHART_TYPE_MAP.get(type_key)
    if mapped is None:
        warnings.append({
            "violation": "unsupported_chart_type",
            "message": (f"chart type {type_key!r} not supported "
                        f"(use one of {sorted(_V5_CHART_TYPE_MAP)}); "
                        f"slot left empty"),
        })
        return warnings

    categories = value.get("categories") or []
    series = value.get("series") or []
    if not categories or not series:
        warnings.append({
            "violation": "empty_chart_data",
            "message": "chart needs at least one category and one series; slot left empty",
        })
        return warnings

    try:
        from pptx.chart.data import CategoryChartData
        from pptx.enum.chart import XL_CHART_TYPE
        cd = CategoryChartData()
        cd.categories = [str(c) for c in categories]
        n_cats = len(categories)
        added = 0
        for s in series:
            if not isinstance(s, dict):
                continue
            name = str(s.get("name") or "")
            raw_vals = s.get("values") or []
            vals = []
            for i in range(n_cats):
                v = raw_vals[i] if i < len(raw_vals) else None
                try:
                    vals.append(float(v) if v is not None else 0.0)
                except (TypeError, ValueError):
                    vals.append(0.0)
            cd.add_series(name, vals)
            added += 1
        if added == 0:
            warnings.append({
                "violation": "empty_chart_data",
                "message": "no usable series after filtering; slot left empty",
            })
            return warnings
        chart_type = getattr(XL_CHART_TYPE, mapped)
        slide.shapes.add_chart(chart_type, left, top, w, h, cd)
    except Exception as e:
        warnings.append({
            "violation": "chart_place_failed",
            "message": f"{type(e).__name__}: {e}; slot left empty",
        })
    return warnings


def cmd_v5_measure_text(args: argparse.Namespace) -> None:
    if args.array:
        try:
            items = json.loads(args.array)
        except json.JSONDecodeError as e:
            raise SystemExit(f"--array must be valid JSON: {e}")
        text = "\n".join(str(x) for x in items)
        n_items = len(items)
    else:
        text = args.text or ""
        n_items = 1
    chars = len(text)
    words = len(text.split())
    lines = text.count("\n") + 1
    out: dict = {"chars": chars, "words": words, "lines_est": lines}
    if args.array:
        out["items"] = n_items
    if args.against:
        try:
            sk_id, slot_id = args.against.split(".", 1)
        except ValueError:
            raise SystemExit("--against must be '<skeleton_id>.<slot_id>'")
        sk = _v5_load_skeleton(sk_id)
        if sk is None:
            raise SystemExit(f"skeleton not found: {sk_id}")
        slot = next((s for s in (sk.get("slots") or []) if s.get("id") == slot_id), None)
        if slot is None:
            raise SystemExit(f"slot {slot_id!r} not in {sk_id}")
        c = slot.get("constraints") or {}
        if args.array:
            fits, reason, hr = _v5_check_bullets_fit(items, c)
            out["fits"] = fits
            out["headroom"] = f"{hr['items']} items to spare" if fits else reason
        else:
            fits, reason, hr_chars = _v5_check_text_fit(text, c)
            out["fits"] = fits
            out["headroom"] = (f"{hr_chars} chars to spare" if fits
                               else f"{abs(hr_chars)} chars over (max {c.get('max_chars')})")
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


# ===========================================================================
# Entry point
# ===========================================================================


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="reader.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_lt = sub.add_parser("list-themes", help="List v5 themes.")
    p_lt.set_defaults(func=cmd_v5_list_themes)

    p_ls = sub.add_parser("list-skeletons", help="List v5 skeletons (filterable).")
    p_ls.add_argument("--category", action="append", default=None,
                      help="filter by category (repeatable; any-match)")
    p_ls.add_argument("--has-slot", action="append", default=None,
                      help="filter by slot kind present (repeatable; any-match)")
    p_ls.add_argument("--status", action="append", default=None,
                      help="status filter (pending/done/rejected; default excludes rejected)")
    p_ls.set_defaults(func=cmd_v5_list_skeletons)

    p_gs = sub.add_parser("get-skeleton", help="Get one v5 skeleton by id.")
    p_gs.add_argument("id")
    p_gs.set_defaults(func=cmd_v5_get_skeleton)

    p_gt = sub.add_parser("get-theme", help="Get one v5 theme by id.")
    p_gt.add_argument("id")
    p_gt.set_defaults(func=cmd_v5_get_theme)

    p_ms = sub.add_parser("match-skeletons",
                          help="Content-first ranked match. Returns matches or rephrase issues.")
    p_ms.add_argument("--content", required=True, help="JSON content dict")
    p_ms.add_argument("--category", action="append", default=None)
    p_ms.add_argument("--has-slot", action="append", default=None)
    p_ms.set_defaults(func=cmd_v5_match_skeletons)

    p_vp = sub.add_parser("validate-plan",
                          help="Pre-build constraint check on a full plan.")
    p_vp.add_argument("plan")
    p_vp.set_defaults(func=cmd_v5_validate_plan)

    p_cf = sub.add_parser("check-asset-fit",
                          help="Does this asset fit this skeleton slot?")
    p_cf.add_argument("asset_id")
    p_cf.add_argument("skeleton_id")
    p_cf.add_argument("slot_id")
    p_cf.set_defaults(func=cmd_v5_check_asset_fit)

    p_fa = sub.add_parser(
        "find-asset",
        help="Deterministic shortlist filter over the asset library "
             "(--kind required; --tags optional, AND-matched). Always "
             "call this BEFORE picking an asset_id by reading index.json.",
    )
    p_fa.add_argument(
        "--kind", required=True,
        help="photo|icon|logo|illustration|screenshot|vector|table|"
             "chart|callout|freeform|smartart",
    )
    p_fa.add_argument(
        "--tags", action="append", default=None,
        help="repeatable; AND-matched against the workspace tag vocab "
             "(see `tag_vocab` field on the find-asset response or in "
             "index.json).",
    )
    p_fa.add_argument(
        "--limit", type=int, default=5,
        help="cap on shortlist size (default 5)",
    )
    p_fa.set_defaults(func=cmd_v5_find_asset)

    p_mt = sub.add_parser("measure-text",
                          help="Char/word/line counts; optional fit against a slot.")
    p_mt.add_argument("text", nargs="?", default=None)
    p_mt.add_argument("--array", default=None, help="JSON array of items (for bullets)")
    p_mt.add_argument("--against", default=None, help="<skeleton_id>.<slot_id>")
    p_mt.set_defaults(func=cmd_v5_measure_text)

    p_cv = sub.add_parser("compose-v5",
                          help="Build a deck from a v5 plan on a chosen host theme.")
    p_cv.add_argument("plan")
    p_cv.add_argument("out")
    p_cv.add_argument("--theme", required=True, help="theme_id (see list-themes)")
    p_cv.add_argument("--force", action="store_true",
                      help="build even when the plan fails validation "
                           "(errors land in the warnings sidecar)")
    p_cv.set_defaults(func=cmd_v5_compose)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
