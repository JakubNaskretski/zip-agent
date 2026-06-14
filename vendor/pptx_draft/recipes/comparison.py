"""comparison — title + labeled left/right (vs, before/after)."""

from __future__ import annotations



def build(content: dict, **params) -> list[dict]:
    title = content.get("title", "")
    left_label = content.get("left_label", "")
    right_label = content.get("right_label", "")
    left_body = content.get("left_body", "")
    right_body = content.get("right_body", "")

    return [
        {
            "type": "heading",
            "level": "h1",
            "grid": {"row": 1, "col": 1, "row_span": 2, "col_span": 12},
            "content": title,
        },
        {
            "type": "heading",
            "level": "h2",
            "color_key": "accent_primary",
            "grid": {"row": 3, "col": 1, "row_span": 1, "col_span": 6},
            "content": left_label,
        },
        {
            "type": "heading",
            "level": "h2",
            "color_key": "accent_primary",
            "grid": {"row": 3, "col": 7, "row_span": 1, "col_span": 6},
            "content": right_label,
        },
        {
            "type": "text",
            "level": "body",
            "grid": {"row": 4, "col": 1, "row_span": 9, "col_span": 6},
            "content": left_body,
        },
        {
            "type": "text",
            "level": "body",
            "grid": {"row": 4, "col": 7, "row_span": 9, "col_span": 6},
            "content": right_body,
        },
    ]
