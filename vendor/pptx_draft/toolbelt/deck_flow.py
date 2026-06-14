"""deck_flow — narrative-level checks across a full plan."""

OPENER_RECIPES = {"title_only"}
CLOSER_RECIPES = {"cta_closing", "quote"}
BULLET_HEAVY = {"title_bullets", "two_col_text", "comparison"}


def deck_flow(plan: dict) -> dict:
    slides = plan.get("slides") or []
    if not slides:
        return {"ok": False, "issues": [{"kind": "empty", "message": "deck has no slides"}]}

    issues = []
    recipes = [s.get("recipe") for s in slides]

    # Opener
    if recipes[0] not in OPENER_RECIPES and recipes[0] != "section_divider":
        issues.append({
            "kind": "no_opener",
            "message": f"Slide 1 is '{recipes[0]}', not an opener.",
            "suggestion": "Start the deck with title_only (or a section_divider).",
        })

    # Closer
    if recipes[-1] not in CLOSER_RECIPES:
        issues.append({
            "kind": "no_closer",
            "message": f"Slide {len(slides)} is '{recipes[-1]}', not a closer.",
            "suggestion": "End the deck with cta_closing or quote.",
        })

    # 3+ same recipe in a row
    run_kind = None
    run_len = 0
    for i, r in enumerate(recipes):
        if r == run_kind:
            run_len += 1
            if run_len == 3 and r in BULLET_HEAVY:
                issues.append({
                    "kind": "monotony",
                    "message": f"Slides {i - 1}-{i + 1} are all '{r}'.",
                    "suggestion": "Break up with a different recipe (image, metric, chart, quote).",
                })
        else:
            run_kind = r
            run_len = 1

    # Section dividers — recommend ≥1 if deck has ≥7 slides
    if len(slides) >= 7 and "section_divider" not in recipes:
        issues.append({
            "kind": "no_sections",
            "message": "Deck is 7+ slides but has no section_divider.",
            "suggestion": "Add section_divider slides to mark major segments.",
        })

    return {
        "ok": len(issues) == 0,
        "slide_count": len(slides),
        "recipe_sequence": recipes,
        "issues": issues,
    }
