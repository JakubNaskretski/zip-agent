"""title_only — cover/opener with centered title + optional subtitle."""

from __future__ import annotations



def build(content: dict, **params) -> list[dict]:
    title = content.get("title", "")
    subtitle = content.get("subtitle")

    placements = [
        {
            "type": "heading",
            "level": "h1",
            "alignment": "center",
            "grid": {"row": 5, "col": 1, "row_span": 3, "col_span": 12},
            "content": title,
        }
    ]
    if subtitle:
        placements.append({
            "type": "heading",
            "level": "h3",
            "alignment": "center",
            "color_key": "text_secondary",
            "grid": {"row": 8, "col": 1, "row_span": 2, "col_span": 12},
            "content": subtitle,
        })
    return placements
