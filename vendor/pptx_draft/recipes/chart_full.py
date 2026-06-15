"""chart_full — title + single chart taking the full slide width.

For when one chart deserves the whole slide and doesn't need a sidebar
of commentary. For chart + commentary use chart_with_takeaway.

content:
  title    (str, required)
  chart    (chart content, required) — {type, categories, series, ...}
  caption  (str, optional)  — short footer attribution / source line

Layout:
  title    rows 1-2  cols 1-12
  chart    rows 3-12 cols 1-12  (full slide width)
  caption  row 12    cols 1-12  (footer, secondary)
"""

from __future__ import annotations


def build(content: dict, **params) -> list[dict]:
    title = content.get("title", "")
    chart = content.get("chart")
    caption = content.get("caption")

    placements = [
        {
            "type": "heading",
            "level": "h1",
            "grid": {"row": 1, "col": 1, "row_span": 2, "col_span": 12},
            "content": title,
        }
    ]
    if chart is not None:
        # If a caption is set, leave the bottom row for it.
        chart_row_span = 9 if caption else 10
        placements.append({
            "type": "chart",
            "grid": {"row": 3, "col": 1,
                     "row_span": chart_row_span, "col_span": 12},
            "content": chart,
        })
    if caption:
        placements.append({
            "type": "text",
            "level": "caption",
            "color_key": "text_secondary",
            "alignment": "left",
            "grid": {"row": 12, "col": 1, "row_span": 1, "col_span": 12},
            "content": caption,
        })
    return placements
