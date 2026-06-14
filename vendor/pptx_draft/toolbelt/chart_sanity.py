"""chart_sanity — flag chart-type / data-shape mismatches."""


def chart_sanity(content: dict) -> dict:
    if not isinstance(content, dict):
        return {"ok": False, "reason": "chart content must be a dict"}
    chart_type = content.get("type", "")
    cats = content.get("categories", []) or []
    series = content.get("series", []) or []
    n_cats = len(cats)
    n_series = len(series)

    issues = []
    suggested_type = None

    if chart_type in ("pie", "doughnut"):
        if n_cats > 6:
            issues.append(f"{chart_type} with {n_cats} categories — hard to read")
            suggested_type = "bar"
        if n_series != 1:
            issues.append(f"{chart_type} expects 1 series; got {n_series}")
            suggested_type = "column"

    if chart_type in ("line", "line_markers"):
        if n_cats < 3:
            issues.append(f"line chart with only {n_cats} categories — use column")
            suggested_type = "column"

    if chart_type in ("bar", "column"):
        if n_series > 5:
            issues.append(f"{chart_type} with {n_series} series — too many to compare cleanly")
            suggested_type = "line" if n_cats >= 4 else "column"

    if n_cats == 0:
        issues.append("no categories provided")
    if n_series == 0:
        issues.append("no series provided")

    ok = not issues
    return {
        "ok": ok,
        "type": chart_type,
        "n_categories": n_cats,
        "n_series": n_series,
        "issues": issues,
        "suggested_type": suggested_type,
        "suggestion": (
            None
            if ok
            else (
                f"Issues: {'; '.join(issues)}. "
                + (f"Consider switching to '{suggested_type}'." if suggested_type else "")
            )
        ),
    }
