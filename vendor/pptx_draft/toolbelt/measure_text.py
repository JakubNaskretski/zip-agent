"""measure_text — estimate whether text fits a cell at a given type-scale level.

Pure approximation: real text metrics depend on installed fonts. For the
agent's purposes, the answer "definitely doesn't fit" is the most important
verdict; "fits with headroom" matters less.

Returns:
  {
    chars, words, lines, fits, overflow_chars, suggested_size_pt,
    safe_chars_at_current_size
  }
"""

from __future__ import annotations

import math


# Char-width-as-fraction-of-em — rough averages for the two families in theme.yaml.
# Serif (Georgia) is narrower than sans (Arial) on average. These are loose
# enough that the agent shouldn't trust 5%-margin answers as fits.
_CHAR_WIDTH_EMS = {
    "Georgia": 0.52,
    "Arial": 0.55,
    "Courier New": 0.60,
    "Inter": 0.54,
    "default": 0.55,
}


def _emu_per_inch():
    return 914400


def _resolve_font_metrics(font_name: str) -> float:
    return _CHAR_WIDTH_EMS.get(font_name, _CHAR_WIDTH_EMS["default"])


def measure_text(
    text: str,
    *,
    type_level: str,
    cell_rect: dict,
    canvas_w_in: float = 13.333,
    canvas_h_in: float = 7.5,
    theme: dict | None = None,
) -> dict:
    """Estimate fit for `text` at theme.type_scale[type_level] in the cell rect.

    cell_rect is fractional {x, y, w, h}.
    """
    if theme is None:
        raise ValueError("theme is required (load theme.yaml)")
    style = theme["type_scale"][type_level]
    font_key = style["font"]
    font_name = theme["fonts"][font_key]
    size_pt = style["size_pt"]
    line_height = style.get("line_height", 1.2)

    # Cell width and height in points (1 inch = 72 pt).
    cell_w_in = cell_rect["w"] * canvas_w_in
    cell_h_in = cell_rect["h"] * canvas_h_in
    cell_w_pt = cell_w_in * 72.0
    cell_h_pt = cell_h_in * 72.0

    # Per-character width and per-line height in points.
    char_w_pt = size_pt * _resolve_font_metrics(font_name)
    line_h_pt = size_pt * line_height

    # Capacity.
    chars_per_line = max(1, int(cell_w_pt // char_w_pt))
    max_lines = max(1, int(cell_h_pt // line_h_pt))
    capacity_chars = chars_per_line * max_lines

    # Measure input.
    text = text or ""
    chars = len(text)
    words = len([w for w in text.split() if w])
    lines = _wrap_estimate(text, chars_per_line)

    fits = lines <= max_lines

    overflow_chars = max(0, chars - capacity_chars)
    safe_chars = capacity_chars

    suggested_size_pt = size_pt
    if not fits:
        # Drop size until lines * line_h fits in cell_h.
        ratio = math.sqrt(capacity_chars / max(1, chars))
        suggested_size_pt = max(8, int(size_pt * ratio))

    return {
        "chars": chars,
        "words": words,
        "lines": lines,
        "max_lines": max_lines,
        "chars_per_line": chars_per_line,
        "safe_chars_at_current_size": safe_chars,
        "overflow_chars": overflow_chars,
        "fits": fits,
        "suggested_size_pt": suggested_size_pt,
        "suggestion": (
            None
            if fits
            else f"Drop ~{chars - safe_chars} chars OR reduce size from "
                 f"{size_pt}pt to ~{suggested_size_pt}pt."
        ),
    }


def _wrap_estimate(text: str, chars_per_line: int) -> int:
    """Wrap text into lines of approx chars_per_line characters, respecting
    word boundaries. Counts lines."""
    if not text:
        return 0
    paragraphs = text.split("\n")
    total = 0
    for para in paragraphs:
        words = para.split()
        if not words:
            total += 1
            continue
        line_len = 0
        line_count = 1
        for w in words:
            wl = len(w)
            if line_len == 0:
                line_len = wl
            elif line_len + 1 + wl <= chars_per_line:
                line_len += 1 + wl
            else:
                line_count += 1
                line_len = wl
        total += line_count
    return total
