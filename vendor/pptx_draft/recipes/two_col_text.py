"""two_col_text — title + two parallel text columns (no labels)."""

from __future__ import annotations



def build(content: dict, **params) -> list[dict]:
    title = content.get("title", "")
    left = content.get("left", "")
    right = content.get("right", "")

    return [
        {
            "type": "heading",
            "level": "h1",
            "grid": {"row": 1, "col": 1, "row_span": 2, "col_span": 12},
            "content": title,
        },
        {
            "type": "text",
            "level": "body",
            "grid": {"row": 3, "col": 1, "row_span": 10, "col_span": 6},
            "content": left,
        },
        {
            "type": "text",
            "level": "body",
            "grid": {"row": 3, "col": 7, "row_span": 10, "col_span": 6},
            "content": right,
        },
    ]
