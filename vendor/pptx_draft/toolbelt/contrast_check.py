"""contrast_check — WCAG 2.1 contrast ratio between two colors."""

from __future__ import annotations



def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def _component(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (_component(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_check(fg_hex: str, bg_hex: str) -> dict:
    """Return WCAG contrast ratio + pass/fail flags.

    Thresholds:
      AA   — 4.5:1 for normal text, 3:1 for large text (18pt+ or 14pt+ bold)
      AAA  — 7:1 for normal text, 4.5:1 for large text
    """
    fg = _relative_luminance(_hex_to_rgb(fg_hex))
    bg = _relative_luminance(_hex_to_rgb(bg_hex))
    lighter = max(fg, bg)
    darker = min(fg, bg)
    ratio = (lighter + 0.05) / (darker + 0.05)
    return {
        "ratio": round(ratio, 2),
        "wcag_aa_normal":  ratio >= 4.5,
        "wcag_aa_large":   ratio >= 3.0,
        "wcag_aaa_normal": ratio >= 7.0,
        "wcag_aaa_large":  ratio >= 4.5,
        "suggestion": (
            None
            if ratio >= 4.5
            else f"Contrast {ratio:.2f}:1 < 4.5:1 (AA). Pick a darker fg or lighter bg."
        ),
    }
