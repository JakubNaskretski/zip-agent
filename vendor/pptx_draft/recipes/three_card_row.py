"""three_card_row — title + 3 cards side-by-side (image + label + body each).

Matches source brand deck's "Three Images Three Content" layout. Bridges
`three_up` (small icons) and `matrix_2x2` (image grid) — use this for
3 items where each item has a full image, not a small icon.

content:
  title  (str, optional)
  items  (list[{image, label, body?}], 3)

Layout:
  title rows 1-2 cols 1-12
  3 cards rows 3-12, cols 1-4 / 5-8 / 9-12 (4 col each, no gaps —
  the per-card internal image-top layout already provides white space
  around each photo).
"""

from __future__ import annotations


def build(content: dict, **params) -> list[dict]:
    title = content.get("title")
    items = list(content.get("items", []))[:3]
    while len(items) < 3:
        items.append({})

    placements = []
    if title:
        placements.append({
            "type": "heading",
            "level": "h1",
            "grid": {"row": 1, "col": 1, "row_span": 2, "col_span": 12},
            "content": title,
        })

    col_starts = [1, 5, 9]
    for col, item in zip(col_starts, items):
        placements.append({
            "type": "card",
            "variant": "image_top",
            "grid": {"row": 3, "col": col, "row_span": 10, "col_span": 4},
            "content": {
                "image": item.get("image"),
                "label": item.get("label", ""),
                "body": item.get("body", ""),
            },
        })

    return placements
