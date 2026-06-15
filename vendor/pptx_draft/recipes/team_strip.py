"""team_strip — title + 4 team cards in a single horizontal row.

Uses the `card` compound component (variant='image_top'). The card handles
internal photo/text proportions — photo on top (~55% of card height), then
name (label) / role (sublabel) / bio (body) stacked below.

content:
  {
    "title": "Our team",
    "members": [
      {"photo": "asset_id" | {"placeholder": true}, "name": "...",
       "role": "...", "bio": "..."?},
      ... up to 4
    ]
  }

Layout:
  title rows 1-2 cols 1-12
  4 cards in a row, each 3 cols wide × 10 rows tall (rows 3-12).
"""

from __future__ import annotations


def build(content: dict, **params) -> list[dict]:
    title = content.get("title", "")
    members = content.get("members", [])[:4]

    placements = []
    if title:
        placements.append({
            "type": "heading",
            "level": "h1",
            "grid": {"row": 1, "col": 1, "row_span": 2, "col_span": 12},
            "content": title,
        })

    col_starts = [1, 4, 7, 10]
    for col, member in zip(col_starts, members):
        placements.append({
            "type": "card",
            "variant": "image_top",
            "grid": {"row": 3, "col": col, "row_span": 10, "col_span": 3},
            "content": {
                "image": member.get("photo"),
                "label": member.get("name", ""),
                "sublabel": member.get("role", ""),
                "body": member.get("bio", ""),
            },
        })

    return placements
