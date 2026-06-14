"""four_up — title + 4 parallel columns (icon + label + body).

Follows the same pattern as three_up, scaled to 4 columns × 3 col_span each.

content:
  title  (str, optional)
  items  (list[{icon_asset_id?, label, body}], 4)

Layout:
  title rows 1-2 cols 1-12
  4 columns at cols 1, 4, 7, 10 (col_span 3 each):
    icon_label  rows 3-5 (3 rows)
    body        rows 6-12 (7 rows)
"""

from __future__ import annotations


def build(content: dict, **params) -> list[dict]:
    title = content.get("title")
    items = list(content.get("items", []))[:4]
    while len(items) < 4:
        items.append({"label": "", "body": "", "icon_asset_id": None})

    placements = []
    body_start_row = 1
    if title:
        placements.append({
            "type": "heading",
            "level": "h1",
            "grid": {"row": 1, "col": 1, "row_span": 2, "col_span": 12},
            "content": title,
        })
        body_start_row = 3

    col_starts = [1, 4, 7, 10]
    # No explicit gap row — icon_label rows 3-5, body rows 6-12. The
    # MIDDLE anchor inside icon_label already provides ~0.80in visual
    # separation between label center and body top.
    for col, item in zip(col_starts, items):
        placements.append({
            "type": "icon_label",
            "grid": {"row": body_start_row, "col": col,
                     "row_span": 3, "col_span": 3},
            "content": {
                "icon_asset_id": item.get("icon_asset_id"),
                "label": item.get("label", ""),
            },
        })
        placements.append({
            "type": "text",
            "level": "body",
            "grid": {"row": body_start_row + 3, "col": col,
                     "row_span": 12 - (body_start_row + 3) + 1,
                     "col_span": 3},
            "content": item.get("body", ""),
        })

    return placements
