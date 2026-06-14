"""grid_audit — verify a slide's component placements are sane on the grid.

Checks:
  - Each placement is within 1..cols / 1..rows after span
  - No two non-spacer placements overlap
"""

from __future__ import annotations


from grid import DEFAULT as DEFAULT_GRID


def _placement_extent(grid: dict) -> tuple[int, int, int, int]:
    """Return (row_start, col_start, row_end_exclusive, col_end_exclusive)."""
    if "strip" in grid:
        # Strips are out-of-grid; treat as no conflict with cells.
        return (-1, -1, -1, -1)
    rs = grid["row"]
    cs = grid["col"]
    re = rs + grid.get("row_span", 1)
    ce = cs + grid.get("col_span", 1)
    return (rs, cs, re, ce)


def _overlaps(a: tuple, b: tuple) -> bool:
    if -1 in a or -1 in b:
        return False
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def grid_audit(slide_components: list[dict], grid=DEFAULT_GRID) -> dict:
    out_of_bounds = []
    overlaps = []
    extents = []
    for i, c in enumerate(slide_components):
        if c.get("type") == "spacer":
            continue
        g = c.get("grid", {})
        if "strip" in g:
            extents.append((i, c.get("type"), (-1, -1, -1, -1)))
            continue
        rs = g.get("row")
        cs = g.get("col")
        re = rs + g.get("row_span", 1)
        ce = cs + g.get("col_span", 1)
        if rs < 1 or cs < 1 or re - 1 > grid.rows or ce - 1 > grid.cols:
            out_of_bounds.append({
                "index": i, "type": c.get("type"),
                "grid": g,
                "reason": "placement exceeds grid bounds",
            })
            continue
        extents.append((i, c.get("type"), (rs, cs, re, ce)))

    for i in range(len(extents)):
        for j in range(i + 1, len(extents)):
            if _overlaps(extents[i][2], extents[j][2]):
                overlaps.append({
                    "a": {"index": extents[i][0], "type": extents[i][1]},
                    "b": {"index": extents[j][0], "type": extents[j][1]},
                })

    ok = not out_of_bounds and not overlaps
    return {
        "ok": ok,
        "out_of_bounds": out_of_bounds,
        "overlaps": overlaps,
        "suggestion": (
            None
            if ok
            else (
                "Resize or move overlapping components. "
                "Recall the grid is 12×12; use cell_to_rect to verify."
            )
        ),
    }
