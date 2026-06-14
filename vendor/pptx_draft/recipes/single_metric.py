"""single_metric — one hero KPI with optional sub-stat + supporting copy.

Matches "Big Data", "Big Data 2", and "Text and Large Stat" patterns from
the source brand deck.

content:
  title      (str, optional)        — page title h1
  value      (str, required)        — the big number/word
  sub_value  (str, optional)        — secondary stat at 66pt directly under
                                      the main value (Big Data pattern)
  caption    (str, optional)        — short attribution / context (footer row)
  body       (str|list, optional)   — supporting paragraph/bullets to the right
  size       ('hero' default | 'mega') — main value type level:
                                      hero = 150pt (default)
                                      mega = 200pt ("Text and Large Stat")

Layout (driven by which optional fields are present):
  title    rows 1-2  cols 1-12
  value    cols 1-8   (vertical center; row_span depends on whether sub_value present)
  sub_value (when present)  cols 1-8  rows below value
  body     cols 9-12  (right column, matching value's vertical extent)
  caption  row 12     cols 1-12   (footer text)
"""

from __future__ import annotations


def build(content: dict, **params) -> list[dict]:
    title = content.get("title")
    value = content.get("value", "")
    sub_value = content.get("sub_value")
    caption = content.get("caption")
    body = content.get("body")
    size = content.get("size", "hero")

    value_level = "mega_metric" if size == "mega" else "hero_metric"

    placements = []
    if title:
        placements.append({
            "type": "heading",
            "level": "h1",
            "grid": {"row": 1, "col": 1, "row_span": 2, "col_span": 12},
            "content": title,
        })

    # Value cell rows depend on whether a sub_value is present + size.
    # The constraint: value text height < value cell height.
    # hero (150pt = 2.08in): fits in 4 rows (2.14in) or more
    # mega (200pt = 2.78in): needs 6+ rows (3.21in)
    if sub_value:
        if size == "mega":
            value_rows = (3, 6)        # rows 3-8 (6 rows)
            sub_rows = (9, 3)          # rows 9-11 (3 rows)
            body_rows = (3, 9)         # rows 3-11 (9 rows on the right)
        else:
            value_rows = (3, 5)        # rows 3-7 (5 rows)
            sub_rows = (8, 3)          # rows 8-10 (3 rows)
            body_rows = (3, 8)         # rows 3-10 (8 rows on the right)
    else:
        value_rows = (3, 8)            # rows 3-10 (8 rows, vertical center)
        sub_rows = None
        body_rows = (3, 8)

    placements.append({
        "type": "heading",
        "level": value_level,
        "color_key": "accent_primary",
        "vertical_anchor": "middle",
        "grid": {"row": value_rows[0], "col": 1,
                 "row_span": value_rows[1], "col_span": 8},
        "content": str(value),
    })

    if sub_rows:
        placements.append({
            "type": "heading",
            "level": "sub_metric",
            "color_key": "accent_secondary",
            "vertical_anchor": "middle",
            "grid": {"row": sub_rows[0], "col": 1,
                     "row_span": sub_rows[1], "col_span": 8},
            "content": str(sub_value),
        })

    if body:
        placements.append({
            "type": "text",
            "level": "body",
            "vertical_anchor": "middle",
            "grid": {"row": body_rows[0], "col": 9,
                     "row_span": body_rows[1], "col_span": 4},
            "content": body,
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
