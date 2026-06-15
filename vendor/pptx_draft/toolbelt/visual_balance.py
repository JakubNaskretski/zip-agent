"""visual_balance — weighted center-of-mass of a slide's components.

Each component contributes ink ∝ its area. We compute the centroid of all
component centers weighted by area. Verdict compares to the slide center
(0.5, 0.5).
"""

from __future__ import annotations



def _placement_metrics(grid: dict) -> tuple[float, float, float]:
    """Return (center_col, center_row, area_cells) — in grid units, ignoring strips."""
    if "strip" in grid:
        return (6.5, 0.5 if grid["strip"] == "header" else 12.5, 1.0)
    rs = grid["row"]; cs = grid["col"]
    rh = grid.get("row_span", 1); cw = grid.get("col_span", 1)
    center_row = rs + rh / 2.0
    center_col = cs + cw / 2.0
    area = rh * cw
    return (center_col, center_row, area)


def visual_balance(slide_components: list[dict]) -> dict:
    weights = []
    cols = []
    rows = []
    for c in slide_components:
        if c.get("type") == "spacer":
            continue
        center_col, center_row, area = _placement_metrics(c.get("grid", {}))
        cols.append(center_col)
        rows.append(center_row)
        weights.append(area)

    if not weights:
        return {"center_of_mass": None, "verdict": "empty"}

    total = sum(weights) or 1.0
    com_col = sum(c * w for c, w in zip(cols, weights)) / total
    com_row = sum(r * w for r, w in zip(rows, weights)) / total

    # Slide center is (col 6.5, row 6.5) on a 12x12.
    drift_col = com_col - 6.5
    drift_row = com_row - 6.5

    verdict_parts = []
    if drift_row < -2:
        verdict_parts.append("top-heavy")
    elif drift_row > 2:
        verdict_parts.append("bottom-heavy")
    if drift_col < -1.5:
        verdict_parts.append("left-weighted")
    elif drift_col > 1.5:
        verdict_parts.append("right-weighted")
    if not verdict_parts:
        verdict_parts.append("balanced")

    return {
        "center_of_mass": {"col": round(com_col, 2), "row": round(com_row, 2)},
        "drift": {"col": round(drift_col, 2), "row": round(drift_row, 2)},
        "verdict": "/".join(verdict_parts),
        "suggestion": (
            None
            if "balanced" in verdict_parts
            else "Add a counterweight or reflow components toward the opposite side."
        ),
    }
