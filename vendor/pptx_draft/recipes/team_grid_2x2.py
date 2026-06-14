"""team_grid_2x2 — title + 2×2 grid of team cards.

Uses the `card` compound component (variant='image_left'). The card handles
internal photo/text spacing — photo left ~30%, gap, name/role/bio stacked
on the right.

content:
  {
    "title": "Leadership team",
    "members": [
      {"photo": "asset_id" | {"placeholder": true}, "name": "...",
       "role": "...", "bio": "..."?},
      ... exactly 4
    ]
  }

Layout:
  title rows 1-2 cols 1-12
  4 cards in 2 rows × 2 cols, with explicit gaps:
    cols gap:   col 6-7 between left/right cards (2-col gap)
    rows gap:   row 7 between top/bottom cards (1-row gap)
    top-left:    rows 3-6  cols 1-5
    top-right:   rows 3-6  cols 8-12
    bottom-left: rows 8-11 cols 1-5
    bottom-right:rows 8-11 cols 8-12
"""

from __future__ import annotations


def build(content: dict, **params) -> list[dict]:
    title = content.get("title", "")
    members = list(content.get("members", []))[:4]
    while len(members) < 4:
        members.append({})

    placements = []
    if title:
        placements.append({
            "type": "heading",
            "level": "h1",
            "grid": {"row": 1, "col": 1, "row_span": 2, "col_span": 12},
            "content": title,
        })

    card_positions = [
        {"row": 3, "col": 1,  "row_span": 4, "col_span": 5},   # top-left
        {"row": 3, "col": 8,  "row_span": 4, "col_span": 5},   # top-right
        {"row": 8, "col": 1,  "row_span": 4, "col_span": 5},   # bottom-left
        {"row": 8, "col": 8,  "row_span": 4, "col_span": 5},   # bottom-right
    ]
    for pos, member in zip(card_positions, members):
        placements.append({
            "type": "card",
            "variant": "image_left",
            "grid": pos,
            "content": {
                "image": member.get("photo"),
                "label": member.get("name", ""),
                "sublabel": member.get("role", ""),
                "body": member.get("bio", ""),
            },
        })

    return placements
