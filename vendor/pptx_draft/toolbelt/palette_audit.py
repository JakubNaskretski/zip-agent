"""palette_audit — flag any explicit colors in a slide spec that don't appear
in the theme's palette / accents / tints / status."""

from __future__ import annotations



def _collect_theme_hexes(theme: dict) -> set[str]:
    out = set()
    for section in ("palette", "tints", "status"):
        for v in theme.get(section, {}).values():
            if isinstance(v, str) and v.startswith("#"):
                out.add(v.upper())
    return out


def _walk_overrides(component: dict, allowed: set[str], offenders: list):
    overrides = component.get("style_overrides") or {}
    for k, v in overrides.items():
        if isinstance(v, str) and v.startswith("#"):
            if v.upper() not in allowed:
                offenders.append({
                    "component": component.get("type"),
                    "field": k,
                    "color": v,
                })


def palette_audit(slide_components: list[dict], theme: dict) -> dict:
    allowed = _collect_theme_hexes(theme)
    offenders = []
    for c in slide_components:
        _walk_overrides(c, allowed, offenders)
    return {
        "ok": len(offenders) == 0,
        "offenders": offenders,
        "suggestion": (
            None
            if not offenders
            else f"{len(offenders)} off-palette color(s). Use color_key references "
                 f"(e.g. 'accent_primary', 'status.positive') instead of explicit hex."
        ),
    }
