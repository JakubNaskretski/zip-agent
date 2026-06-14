"""big_statement — large declarative text, optional sub-line.

Uses the 'statement' type level (66pt). For hero / cover-style proclamations:
"We're not slowing down." / "$1B in pipeline." / "Three things matter."

content:
  statement   (str, required)
  sub         (str, optional)               — smaller line below
  alignment   ('left' default | 'center')

Layout:
  statement rows 3-9 cols 1-12 (left: cols 1-12 still — uses full width
            for headroom; alignment controls text alignment, not cell)
  sub       rows 10-11 cols 1-12

Statement cell is 7 rows tall (3.75in) to accommodate up to ~3 lines at
66pt × 1.10 line height = ~3.0in. Anything longer should be split across
two slides or rephrased.
"""

from __future__ import annotations


def build(content: dict, **params) -> list[dict]:
    statement = content.get("statement", "")
    sub = content.get("sub")
    alignment = content.get("alignment", "left")

    placements = [
        {
            "type": "heading",
            "level": "statement",
            "alignment": alignment,
            "vertical_anchor": "middle",
            "grid": {"row": 3, "col": 1, "row_span": 7, "col_span": 12},
            "content": statement,
        }
    ]
    if sub:
        placements.append({
            "type": "text",
            "level": "body",
            "alignment": alignment,
            "color_key": "text_secondary",
            "grid": {"row": 10, "col": 1, "row_span": 2, "col_span": 12},
            "content": sub,
        })
    return placements
