"""quote — big pull-quote + attribution, centered horizontally."""

from __future__ import annotations



def build(content: dict, **params) -> list[dict]:
    text = content.get("text", "")
    attribution = content.get("attribution", "")

    return [
        {
            "type": "quote",
            "grid": {"row": 4, "col": 2, "row_span": 6, "col_span": 10},
            "content": {"text": text, "attribution": attribution},
        }
    ]
