"""matrix_2x2 — title + 2×2 grid of cards (image + label + body).

Generic 2×2 layout for any image-and-content matrix. team_grid_2x2 is the
team-specific specialization of this pattern.

Uses the `card` compound component (variant='image_left'), which handles
internal image/text proportions and the gap between image and text.
This recipe just decides where to put each card on the grid.

content:
  title  (str, optional)
  items  (list[{image, label, body?}], 4)
         image can be 'asset_id' string OR {asset_id, fit} OR {placeholder, label}

Layout:
  title rows 1-2 cols 1-12
  Cards in 2 rows × 2 cols. Each card is 6 cols wide × 4 rows tall,
  with a 1-row gap between top/bottom rows so cards don't visually touch.
    top-left:     rows 3-6  cols 1-6
    top-right:    rows 3-6  cols 7-12
    bottom-left:  rows 8-11 cols 1-6
    bottom-right: rows 8-11 cols 7-12
"""

from __future__ import annotations


def build(content: dict, **params) -> list[dict]:
    title = content.get("title")
    items = list(content.get("items", []))[:4]
    while len(items) < 4:
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
        {"row": 3, "col": 1, "row_span": 4, "col_span": 6},   # top-left
        {"row": 3, "col": 7, "row_span": 4, "col_span": 6},   # top-right
        {"row": 8, "col": 1, "row_span": 4, "col_span": 6},   # bottom-left
        {"row": 8, "col": 7, "row_span": 4, "col_span": 6},   # bottom-right
    ]
    for pos, item in zip(card_positions, items):
        placements.append({
            "type": "card",
            "variant": "image_left",
            "grid": pos,
            "content": {
                "image": item.get("image"),
                "label": item.get("label", ""),
                "body": item.get("body", ""),
            },
        })

    return placements
