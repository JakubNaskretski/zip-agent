"""table_full — title + full-width table."""

from __future__ import annotations



def build(content: dict, **params) -> list[dict]:
    title = content.get("title", "")
    table = content.get("table")

    placements = [
        {
            "type": "heading",
            "level": "h1",
            "grid": {"row": 1, "col": 1, "row_span": 2, "col_span": 12},
            "content": title,
        }
    ]
    if table is not None:
        placements.append({
            "type": "table",
            "grid": {"row": 3, "col": 1, "row_span": 10, "col_span": 12},
            "content": table,
        })
    return placements
