"""three_up — optional title + three parallel columns (icon + label + body)."""

from __future__ import annotations



def build(content: dict, **params) -> list[dict]:
    title = content.get("title", "")
    items = content.get("items", [])
    items = items[:3]
    while len(items) < 3:
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

    col_starts = [1, 5, 9]
    for col, item in zip(col_starts, items):
        # Icon + label band
        placements.append({
            "type": "icon_label",
            "grid": {"row": body_start_row, "col": col,
                     "row_span": 3, "col_span": 4},
            "content": {
                "icon_asset_id": item.get("icon_asset_id"),
                "label": item.get("label", ""),
            },
        })
        # Body text below
        placements.append({
            "type": "text",
            "level": "body",
            "grid": {"row": body_start_row + 3, "col": col,
                     "row_span": 12 - (body_start_row + 3) + 1, "col_span": 4},
            "content": item.get("body", ""),
        })

    return placements
