"""metric_strip — 2-4 KPIs displayed side-by-side.

Count is derived from len(content['metrics']). Cell allocation:
  2 metrics: cols 1-6, 7-12 (6 col span each)
  3 metrics: cols 1-4, 5-8, 9-12 (4 col span each)
  4 metrics: cols 1-3, 4-6, 7-9, 10-12 (3 col span each)
"""

from __future__ import annotations



def build(content: dict, **params) -> list[dict]:
    title = content.get("title", "")
    metrics = content.get("metrics", [])
    if not metrics:
        return []
    n = min(len(metrics), 4)
    metrics = metrics[:n]

    if n == 2:
        spans = [(1, 6), (7, 6)]
    elif n == 3:
        spans = [(1, 4), (5, 4), (9, 4)]
    else:  # 4
        spans = [(1, 3), (4, 3), (7, 3), (10, 3)]

    placements = []
    if title:
        placements.append({
            "type": "heading",
            "level": "h1",
            "grid": {"row": 1, "col": 1, "row_span": 2, "col_span": 12},
            "content": title,
        })

    metric_row = 5
    metric_row_span = 5
    for (col, col_span), m in zip(spans, metrics):
        placements.append({
            "type": "metric",
            "grid": {"row": metric_row, "col": col,
                     "row_span": metric_row_span, "col_span": col_span},
            "content": m,
        })

    return placements
