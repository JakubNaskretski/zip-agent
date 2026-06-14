"""cta_closing — closing slide with title, CTA button, optional contact line."""

from __future__ import annotations



def build(content: dict, **params) -> list[dict]:
    title = content.get("title", "")
    cta = content.get("cta")
    contact = content.get("contact", "")

    placements = [
        {
            "type": "heading",
            "level": "h1",
            "alignment": "center",
            "grid": {"row": 3, "col": 1, "row_span": 3, "col_span": 12},
            "content": title,
        }
    ]

    if cta:
        placements.append({
            "type": "cta",
            "grid": {"row": 7, "col": 5, "row_span": 2, "col_span": 4},
            "content": cta,
        })

    if contact:
        placements.append({
            "type": "text",
            "level": "caption",
            "alignment": "center",
            "color_key": "text_secondary",
            "grid": {"row": 10, "col": 1, "row_span": 2, "col_span": 12},
            "content": contact,
        })

    return placements
