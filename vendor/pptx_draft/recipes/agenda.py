"""agenda — title + numbered list of agenda items.

content:
  title  (str, optional)
  items  (list[str], required, 1-10)

Layout:
  title  rows 1-2 cols 1-12
  Each item: number (h2 in accent_primary, col 1) + label (h3, cols 2-12).
  1-5 items: 2 rows tall each (generous spacing).
  6-10 items: 1 row tall each (compressed).
"""

from __future__ import annotations


def build(content: dict, **params) -> list[dict]:
    title = content.get("title")
    items = content.get("items", [])
    n = max(0, min(len(items), 10))
    items = items[:n]

    placements = []
    if title:
        placements.append({
            "type": "heading",
            "level": "h1",
            "grid": {"row": 1, "col": 1, "row_span": 2, "col_span": 12},
            "content": title,
        })

    if n == 0:
        return placements

    row_span = 2 if n <= 5 else 1
    start_row = 3
    for i, item in enumerate(items):
        r = start_row + i * row_span
        # Number (1-indexed)
        placements.append({
            "type": "heading",
            "level": "h2",
            "color_key": "accent_primary",
            "grid": {"row": r, "col": 1, "row_span": row_span, "col_span": 1},
            "content": f"{i + 1}.",
        })
        # Label
        placements.append({
            "type": "heading",
            "level": "h3" if row_span == 2 else "body",
            "grid": {"row": r, "col": 2, "row_span": row_span, "col_span": 11},
            "content": item,
        })

    return placements
