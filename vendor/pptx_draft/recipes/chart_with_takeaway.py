"""chart_with_takeaway — title + chart (left) + takeaway text (right sidebar)."""

from __future__ import annotations



def build(content: dict, **params) -> list[dict]:
    title = content.get("title", "")
    chart = content.get("chart")
    takeaway = content.get("takeaway", "")

    placements = [
        {
            "type": "heading",
            "level": "h1",
            "grid": {"row": 1, "col": 1, "row_span": 2, "col_span": 12},
            "content": title,
        }
    ]
    if chart is not None:
        placements.append({
            "type": "chart",
            "grid": {"row": 3, "col": 1, "row_span": 10, "col_span": 8},
            "content": chart,
        })
    placements.append({
        "type": "text",
        "level": "body",
        "grid": {"row": 3, "col": 9, "row_span": 10, "col_span": 4},
        "content": takeaway,
    })
    return placements
