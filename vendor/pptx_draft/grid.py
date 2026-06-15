"""Grid math.

Canvas divides into:
  - 0.5 col + 12 cols + 0.5 col = 13 col-units horizontally
  - 1 row + 12 rows + 1 row    = 14 row-units vertically

Cell (R, C) with R, C in 1..12:
  x = (0.5 + (C - 1)) / 13
  y = (1   + (R - 1)) / 14
  w = col_span / 13
  h = row_span / 14

All outputs are FRACTIONAL coordinates (0..1). The renderer converts to
EMUs against the actual canvas size.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class GridConfig:
    cols: int = 12
    rows: int = 12
    left_margin_cols: float = 0.5
    right_margin_cols: float = 0.5
    top_strip_rows: int = 1
    bottom_strip_rows: int = 1

    @property
    def total_col_units(self) -> float:
        return self.cols + self.left_margin_cols + self.right_margin_cols

    @property
    def total_row_units(self) -> float:
        return self.rows + self.top_strip_rows + self.bottom_strip_rows


DEFAULT = GridConfig()


def cell_to_rect(
    row: int,
    col: int,
    row_span: int = 1,
    col_span: int = 1,
    grid: GridConfig = DEFAULT,
) -> dict:
    """Resolve a grid placement to a fractional rect.

    row/col are 1-indexed. (1,1) is the top-left content cell, NOT the
    top-left corner of the canvas (which is part of the top strip).
    """
    if row < 1 or row > grid.rows:
        raise ValueError(f"row {row} out of range 1..{grid.rows}")
    if col < 1 or col > grid.cols:
        raise ValueError(f"col {col} out of range 1..{grid.cols}")
    if row + row_span - 1 > grid.rows:
        raise ValueError(f"row_span {row_span} from row {row} overflows grid")
    if col + col_span - 1 > grid.cols:
        raise ValueError(f"col_span {col_span} from col {col} overflows grid")

    x = (grid.left_margin_cols + (col - 1)) / grid.total_col_units
    y = (grid.top_strip_rows + (row - 1)) / grid.total_row_units
    w = col_span / grid.total_col_units
    h = row_span / grid.total_row_units
    return {"x": x, "y": y, "w": w, "h": h}


def header_strip_rect(grid: GridConfig = DEFAULT) -> dict:
    """The top reserved strip — full-width, height = 1 row-unit."""
    return {
        "x": 0.0,
        "y": 0.0,
        "w": 1.0,
        "h": grid.top_strip_rows / grid.total_row_units,
    }


def footer_strip_rect(grid: GridConfig = DEFAULT) -> dict:
    """The bottom reserved strip — full-width, height = 1 row-unit."""
    return {
        "x": 0.0,
        "y": (grid.top_strip_rows + grid.rows) / grid.total_row_units,
        "w": 1.0,
        "h": grid.bottom_strip_rows / grid.total_row_units,
    }


def placement_to_rect(placement: dict, grid: GridConfig = DEFAULT) -> dict:
    """Convenience: takes a placement dict from a recipe or free-mode
    component spec and returns the resolved fractional rect.

    Accepts:
      {"row": R, "col": C, "row_span": RS, "col_span": CS}   # normal
      {"strip": "header"|"footer"}                            # reserved strips
    """
    if "strip" in placement:
        if placement["strip"] == "header":
            return header_strip_rect(grid)
        if placement["strip"] == "footer":
            return footer_strip_rect(grid)
        raise ValueError(f"unknown strip: {placement['strip']}")
    return cell_to_rect(
        placement["row"],
        placement["col"],
        placement.get("row_span", 1),
        placement.get("col_span", 1),
        grid,
    )
