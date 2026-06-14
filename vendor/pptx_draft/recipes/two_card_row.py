"""two_card_row — title + 2 cards side-by-side (image + label + body each).

Matches source brand deck's "Two Content with Two Images" layout. Uses
the `card` compound component (variant=image_top) so each card handles
its own internal image/text proportions.

content:
  title  (str, optional)
  items  (list[{image, label, body?}], 2)
         image: 'asset_id' string OR {asset_id, fit} OR {placeholder, label}

Layout:
  title rows 1-2 cols 1-12
  Cards rows 3-12, each 5 cols wide with a 2-col gap between:
    left card:  cols 1-5
    right card: cols 8-12
"""

from __future__ import annotations


def build(content: dict, **params) -> list[dict]:
    title = content.get("title")
    items = list(content.get("items", []))[:2]
    while len(items) < 2:
        items.append({})

    placements = []
    if title:
        placements.append({
            "type": "heading",
            "level": "h1",
            "grid": {"row": 1, "col": 1, "row_span": 2, "col_span": 12},
            "content": title,
        })

    card_positions = [
        {"row": 3, "col": 1, "row_span": 10, "col_span": 5},   # left
        {"row": 3, "col": 8, "row_span": 10, "col_span": 5},   # right
    ]
    for pos, item in zip(card_positions, items):
        placements.append({
            "type": "card",
            "variant": "image_top",
            "grid": pos,
            "content": {
                "image": item.get("image"),
                "label": item.get("label", ""),
                "body": item.get("body", ""),
            },
        })

    return placements
