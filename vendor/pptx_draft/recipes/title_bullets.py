"""title_bullets — title + bullet list. The workhorse content slide."""

from __future__ import annotations



def build(content: dict, **params) -> list[dict]:
    title = content.get("title", "")
    bullets = content.get("bullets", [])

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
            "grid": {"row": 3, "col": 1, "row_span": 10, "col_span": 12},
            "content": bullets if isinstance(bullets, list) else [bullets],
        },
    ]
