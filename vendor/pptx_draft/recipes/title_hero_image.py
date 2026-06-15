"""title_hero_image — title + single full-width image below."""

from __future__ import annotations



def build(content: dict, **params) -> list[dict]:
    title = content.get("title", "")
    image = content.get("image", {"placeholder": True, "label": "hero image"})

    return [
        {
            "type": "heading",
            "level": "h1",
            "grid": {"row": 1, "col": 1, "row_span": 2, "col_span": 12},
            "content": title,
        },
        {
            "type": "image",
            "grid": {"row": 3, "col": 1, "row_span": 10, "col_span": 12},
            "content": image,
        },
    ]
