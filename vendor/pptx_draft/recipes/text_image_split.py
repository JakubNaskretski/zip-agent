"""text_image_split — title + text on one side, image on the other.

Default image_side='right'. Set image_side='left' to mirror.
"""

from __future__ import annotations



def build(content: dict, **params) -> list[dict]:
    title = content.get("title", "")
    text = content.get("text", "")
    image = content.get("image", {"placeholder": True, "label": "image"})
    image_side = params.get("image_side", "right")

    if image_side == "right":
        text_grid = {"row": 3, "col": 1, "row_span": 10, "col_span": 6}
        image_grid = {"row": 3, "col": 7, "row_span": 10, "col_span": 6}
    else:
        image_grid = {"row": 3, "col": 1, "row_span": 10, "col_span": 6}
        text_grid = {"row": 3, "col": 7, "row_span": 10, "col_span": 6}

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
            "grid": text_grid,
            "content": text,
        },
        {
            "type": "image",
            "grid": image_grid,
            "content": image,
        },
    ]
