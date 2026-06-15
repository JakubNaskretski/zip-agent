"""table_with_callout — title + callout text (left half) + table (right half).

Matches the source deck's slides 1-2 layout where a branded table sits in
the right portion of the slide with a heading + supporting bullets on the
left. Pass table['style'] to pick a branded look:
filled_accent | filled_neutral | header_accent | zebra_neutral | minimal |
underline_accent | underline_neutral.
"""

from __future__ import annotations


def build(content: dict, **params) -> list[dict]:
    title = content.get("title", "")
    callout_heading = content.get("callout_heading", "")
    callout_body = content.get("callout_body", "")
    table = content.get("table")

    placements = [
        {
            "type": "heading",
            "level": "h1",
            "grid": {"row": 1, "col": 1, "row_span": 2, "col_span": 12},
            "content": title,
        }
    ]
    if callout_heading:
        placements.append({
            "type": "heading",
            "level": "h2",
            "color_key": "accent_primary",
            "grid": {"row": 3, "col": 1, "row_span": 2, "col_span": 5},
            "content": callout_heading,
        })
    if callout_body:
        placements.append({
            "type": "text",
            "level": "body",
            "grid": {"row": 5, "col": 1, "row_span": 8, "col_span": 5},
            "content": callout_body,
        })
    if table is not None:
        placements.append({
            "type": "table",
            "grid": {"row": 3, "col": 6, "row_span": 10, "col_span": 7},
            "content": table,
        })
    return placements
