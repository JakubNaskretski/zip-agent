"""numbered_list_6up — title + 6 numbered items in a single horizontal row.

Matches source brand deck's "Six Content Six Numbers" layout: 6 items
side by side, each with a 115pt numeral on top and label+body stacked
below. Each item is narrow (2 cols) but gets the full slide height for
text, so body can run several lines per item.

content:
  title  (str, optional)
  items  (list[{number?, label, body}], 1-6)
         number defaults to the 1-indexed position; pass 'number' to override.

Layout:
  title rows 1-2 cols 1-12
  6 items in one row at cols (1,3,5,7,9,11), col_span 2 each:
    number rows 3-5  (3 rows = 1.61in) at numbered_item 115pt, accent_primary
    label  row  6    (1 row)  at h3 18pt
    body   rows 7-12 (6 rows) at body 15pt
  (Item text widths are narrow at 2 cols — keep labels short and body
   sentences compact.)
"""

from __future__ import annotations


def build(content: dict, **params) -> list[dict]:
    title = content.get("title")
    items = list(content.get("items", []))[:6]

    placements = []
    if title:
        placements.append({
            "type": "heading",
            "level": "h1",
            "grid": {"row": 1, "col": 1, "row_span": 2, "col_span": 12},
            "content": title,
        })

    col_starts = [1, 3, 5, 7, 9, 11]
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            item = {"label": str(item), "body": ""}
        col = col_starts[i]
        number = item.get("number") or str(i + 1)
        # Number: 3 rows tall, 115pt, accent_primary
        placements.append({
            "type": "heading",
            "level": "numbered_item",
            "color_key": "accent_primary",
            "grid": {"row": 3, "col": col, "row_span": 3, "col_span": 2},
            "content": str(number),
        })
        # Label: 1 row, h3
        placements.append({
            "type": "heading",
            "level": "h3",
            "grid": {"row": 6, "col": col, "row_span": 1, "col_span": 2},
            "content": item.get("label", ""),
        })
        # Body: 6 rows, body text — plenty of vertical room for multi-line
        body = item.get("body")
        if body:
            placements.append({
                "type": "text",
                "level": "body",
                "grid": {"row": 7, "col": col, "row_span": 6, "col_span": 2},
                "content": body,
            })

    return placements
