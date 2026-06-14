"""Recipe registry.

A recipe is a parametric function that takes content (and optional params)
and returns a list of component placements. The render engine resolves
placements to absolute coordinates via grid.py and dispatches each one to
its component renderer.

To add a recipe: create `recipes/<name>.py` exporting `build(content, **params)`
and add it to RECIPES below. Each signature in RECIPE_SIGNATURES includes
an `example` of a minimal valid `content` (and `params`) payload so the
agent can copy-paste-modify without inspecting recipe source.
"""

from . import (
    title_only,
    section_divider,
    title_bullets,
    title_hero_image,
    text_image_split,
    two_col_text,
    comparison,
    metric_strip,
    single_metric,
    big_statement,
    agenda,
    numbered_list_6up,
    chart_with_takeaway,
    chart_full,
    table_full,
    table_with_callout,
    three_up,
    four_up,
    six_up,
    matrix_2x2,
    two_card_row,
    three_card_row,
    quote,
    cta_closing,
    team_strip,
    team_grid_2x2,
)


RECIPES = {
    "title_only": title_only.build,
    "section_divider": section_divider.build,
    "title_bullets": title_bullets.build,
    "title_hero_image": title_hero_image.build,
    "text_image_split": text_image_split.build,
    "two_col_text": two_col_text.build,
    "comparison": comparison.build,
    "metric_strip": metric_strip.build,
    "single_metric": single_metric.build,
    "big_statement": big_statement.build,
    "agenda": agenda.build,
    "numbered_list_6up": numbered_list_6up.build,
    "chart_with_takeaway": chart_with_takeaway.build,
    "chart_full": chart_full.build,
    "table_full": table_full.build,
    "table_with_callout": table_with_callout.build,
    "three_up": three_up.build,
    "four_up": four_up.build,
    "six_up": six_up.build,
    "matrix_2x2": matrix_2x2.build,
    "two_card_row": two_card_row.build,
    "three_card_row": three_card_row.build,
    "quote": quote.build,
    "cta_closing": cta_closing.build,
    "team_strip": team_strip.build,
    "team_grid_2x2": team_grid_2x2.build,
}


RECIPE_SIGNATURES = {
    "title_only": {
        "content": {"title": "str (required)", "subtitle": "str (optional)"},
        "params": {},
        "use_when": "Cover / opener slide.",
        "example": {
            "content": {"title": "Q4 results", "subtitle": "FY25 wrap-up"},
        },
    },
    "section_divider": {
        "content": {
            "number": "str|int (required)",
            "label": "str (required)",
            "alignment": "'right' (default) | 'left' | 'center'",
        },
        "params": {},
        "use_when": "Visual break between major sections. Big orange numeral + section label. Position varies by alignment.",
        "example": {
            "content": {"number": "01", "label": "Market context", "alignment": "right"},
        },
    },
    "title_bullets": {
        "content": {"title": "str (required)", "bullets": "list[str] (required, max 6)"},
        "params": {},
        "use_when": "Bread-and-butter content slide. Title + bullets.",
        "example": {
            "content": {
                "title": "Why now",
                "bullets": [
                    "Demand peaked in Q3",
                    "Two competitors retrenched",
                    "Pricing window open for ~2 quarters",
                ],
            },
        },
    },
    "title_hero_image": {
        "content": {"title": "str (required)", "image": "image content (required)"},
        "params": {},
        "use_when": "Single big visual under a title.",
        "example": {
            "content": {
                "title": "Our new HQ",
                "image": {"asset_id": "hero_office", "fit": "fill"},
            },
        },
    },
    "text_image_split": {
        "content": {"title": "str (required)", "text": "str or list[str]", "image": "image content"},
        "params": {"image_side": "'left'|'right' (default 'right')"},
        "use_when": "Text on one side, image on the other.",
        "example": {
            "content": {
                "title": "Operating model",
                "text": ["Three-pillar org", "Local accountability", "Shared platform"],
                "image": {"asset_id": "team_offsite", "fit": "fill"},
            },
            "params": {"image_side": "right"},
        },
    },
    "two_col_text": {
        "content": {"title": "str (required)", "left": "str or list[str]", "right": "str or list[str]"},
        "params": {},
        "use_when": "Two parallel columns of text without labels.",
        "example": {
            "content": {
                "title": "Decisions today",
                "left":  ["Greenlight Berlin office", "Hire 12 GTMs by Q2"],
                "right": ["Sunset legacy SKUs", "Cut three vendors"],
            },
        },
    },
    "comparison": {
        "content": {
            "title": "str (required)",
            "left_label": "str (required)",
            "right_label": "str (required)",
            "left_body": "str or list[str]",
            "right_body": "str or list[str]",
        },
        "params": {},
        "use_when": "Labeled vs / before-after comparison.",
        "example": {
            "content": {
                "title": "Build vs buy",
                "left_label": "Build",
                "right_label": "Buy",
                "left_body":  ["Full control", "12 mo to ship", "$2M build cost"],
                "right_body": ["Off the shelf", "1 mo to ship", "$400k/yr"],
            },
        },
    },
    "metric_strip": {
        "content": {
            "title": "str (optional)",
            "metrics": "list[{value, label, delta?, delta_status?}] (2-4 items)",
        },
        "params": {},
        "use_when": "2-4 KPIs displayed side-by-side. Count derived from len(metrics).",
        "example": {
            "content": {
                "title": "By the numbers",
                "metrics": [
                    {"value": "$1.8M", "label": "Revenue", "delta": "+12%", "delta_status": "positive"},
                    {"value": "32%",   "label": "Margin",  "delta": "+200bps", "delta_status": "positive"},
                    {"value": "$0.4M", "label": "FCF"},
                ],
            },
        },
    },
    "chart_with_takeaway": {
        "content": {
            "title": "str (required)",
            "chart": "chart content (required)",
            "takeaway": "str or list[str]",
        },
        "params": {},
        "use_when": "A chart plus a sentence/list of takeaways in a sidebar.",
        "example": {
            "content": {
                "title": "Quarterly revenue",
                "chart": {
                    "type": "bar",
                    "categories": ["Q1", "Q2", "Q3", "Q4"],
                    "series": [{"name": "Revenue", "values": [1.2, 1.4, 1.6, 1.8]}],
                },
                "takeaway": ["Up and to the right.", "Q4 momentum holds into Q1."],
            },
        },
    },
    "table_full": {
        "content": {
            "title": "str (required)",
            "table": "{rows, cols, has_header, data: [[...]], style?}",
        },
        "params": {},
        "use_when": "Title + full-width table. table.style: header_accent (default) | zebra_neutral | filled_accent | filled_neutral | minimal | underline_accent | underline_neutral.",
        "example": {
            "content": {
                "title": "Coverage by region",
                "table": {
                    "rows": 3, "cols": 3, "has_header": True,
                    "data": [
                        ["Region", "Q3", "Q4"],
                        ["EMEA",   "$1.1M", "$1.4M"],
                        ["APAC",   "$0.5M", "$0.7M"],
                    ],
                    "style": "header_accent",
                },
            },
        },
    },
    "table_with_callout": {
        "content": {
            "title": "str (required)",
            "callout_heading": "str (optional, left h2)",
            "callout_body": "str|list[str] (optional, left bullets)",
            "table": "{rows, cols, has_header, data: [[...]], style?}",
        },
        "params": {},
        "use_when": "Title + callout text (left half) + branded table (right half). Matches the source deck's branded-table layout.",
        "example": {
            "content": {
                "title": "Coverage by region",
                "callout_heading": "Where we're winning",
                "callout_body": ["EMEA up 27%", "APAC up 40% off a low base"],
                "table": {
                    "rows": 3, "cols": 3, "has_header": True,
                    "data": [
                        ["Region", "Q3", "Q4"],
                        ["EMEA",   "$1.1M", "$1.4M"],
                        ["APAC",   "$0.5M", "$0.7M"],
                    ],
                },
            },
        },
    },
    "three_up": {
        "content": {
            "title": "str (optional)",
            "items": "list[{icon_asset_id?, label, body}] (3 items)",
        },
        "params": {},
        "use_when": "Three parallel columns each with icon, label, supporting body.",
        "example": {
            "content": {
                "title": "Our pillars",
                "items": [
                    {"icon_asset_id": "icon_security", "label": "Trust",  "body": "Day-one encryption"},
                    {"icon_asset_id": "icon_speed",    "label": "Speed",  "body": "P95 < 100ms"},
                    {"icon_asset_id": "icon_scale",    "label": "Scale",  "body": "Petabyte ingest"},
                ],
            },
        },
    },
    "quote": {
        "content": {"text": "str (required)", "attribution": "str (optional)"},
        "params": {},
        "use_when": "Pull-quote or testimonial.",
        "example": {
            "content": {
                "text": "Make it work, make it right, make it fast.",
                "attribution": "Kent Beck",
            },
        },
    },
    "cta_closing": {
        "content": {
            "title": "str (required)",
            "cta": "{label: str}",
            "contact": "str (optional)",
        },
        "params": {},
        "use_when": "Closing / next-steps / thank-you slide.",
        "example": {
            "content": {
                "title": "Let's ship Q1",
                "cta": {"label": "Approve plan"},
                "contact": "ceo@acme.com",
            },
        },
    },
    "team_strip": {
        "content": {
            "title": "str (optional)",
            "members": "list[{photo, name, role, bio?}] (up to 4, one row)",
        },
        "params": {},
        "use_when": "Up to 4 team members across one row. Photos top, name + role + bio below each. Matches source deck's slide-5 layout.",
        "example": {
            "content": {
                "title": "Leadership",
                "members": [
                    {"photo": {"asset_id": "headshot_ana"}, "name": "Ana López", "role": "CEO"},
                    {"photo": {"asset_id": "headshot_jin"}, "name": "Jin Park",  "role": "CFO"},
                    {"photo": {"asset_id": "headshot_mei"}, "name": "Mei Tan",   "role": "CPO"},
                ],
            },
        },
    },
    "team_grid_2x2": {
        "content": {
            "title": "str (optional)",
            "members": "list[{photo, name, role, bio?}] (exactly 4)",
        },
        "params": {},
        "use_when": "4 team members in 2x2 grid with photo-left, text-right. More room for bio than team_strip. Matches source deck's slide-6 layout.",
        "example": {
            "content": {
                "title": "Leadership",
                "members": [
                    {"photo": {"asset_id": "headshot_ana"}, "name": "Ana López", "role": "CEO", "bio": "10y in B2B SaaS."},
                    {"photo": {"asset_id": "headshot_jin"}, "name": "Jin Park",  "role": "CFO", "bio": "Ex-fintech FP&A."},
                    {"photo": {"asset_id": "headshot_mei"}, "name": "Mei Tan",   "role": "CPO", "bio": "Shipped 4 platforms."},
                    {"photo": {"asset_id": "headshot_kai"}, "name": "Kai Singh", "role": "CTO", "bio": "Distributed systems."},
                ],
            },
        },
    },
    "single_metric": {
        "content": {
            "title": "str (optional)",
            "value": "str (required)",
            "sub_value": "str (optional, 66pt secondary stat directly below)",
            "caption": "str (optional, footer)",
            "body": "str|list (optional, supporting copy on the right)",
            "size": "'hero' (default, 150pt) | 'mega' (200pt — dominates slide)",
        },
        "params": {},
        "use_when": "One hero KPI dominates the slide. For 2-4 KPIs use metric_strip instead. Use size='mega' when the stat alone is the headline.",
        "example": {
            "content": {
                "title": "Annual revenue",
                "value": "$1.8M",
                "sub_value": "+12% YoY",
                "caption": "Audited Nov 2025",
                "size": "hero",
            },
        },
    },
    "big_statement": {
        "content": {
            "statement": "str (required)",
            "sub": "str (optional, smaller line below)",
            "alignment": "'left' (default) | 'center'",
        },
        "params": {},
        "use_when": "Big declarative text statement. Smaller than section_divider, bigger than h1 (~60pt).",
        "example": {
            "content": {
                "statement": "We will be cash-flow positive by Q3.",
                "sub": "Backed by recurring revenue from the top 30 accounts.",
                "alignment": "left",
            },
        },
    },
    "agenda": {
        "content": {
            "title": "str (optional)",
            "items": "list[str] (1-10)",
        },
        "params": {},
        "use_when": "Numbered TOC / agenda list. 1-5 items get generous spacing; 6+ get compressed.",
        "example": {
            "content": {
                "title": "Today",
                "items": ["Market context", "Our plan", "What we need from you"],
            },
        },
    },
    "numbered_list_6up": {
        "content": {
            "title": "str (optional)",
            "items": "list[{number?, label, body}] (1-6)",
        },
        "params": {},
        "use_when": "6 items each with a prominent numeral, label, and body. Use when the numbers themselves carry meaning (steps, principles, priorities).",
        "example": {
            "content": {
                "title": "Our principles",
                "items": [
                    {"number": "01", "label": "Speed",      "body": "Ship daily, learn weekly"},
                    {"number": "02", "label": "Trust",      "body": "Earn it slide by slide"},
                    {"number": "03", "label": "Focus",      "body": "Say no to 9 of 10"},
                    {"number": "04", "label": "Ownership",  "body": "Author what you ship"},
                    {"number": "05", "label": "Clarity",    "body": "Plain words win"},
                    {"number": "06", "label": "Curiosity",  "body": "Ask why twice"},
                ],
            },
        },
    },
    "four_up": {
        "content": {
            "title": "str (optional)",
            "items": "list[{icon_asset_id?, label, body}] (4)",
        },
        "params": {},
        "use_when": "4 parallel columns each with icon, label, body. Same pattern as three_up.",
        "example": {
            "content": {
                "title": "Workflow",
                "items": [
                    {"icon_asset_id": "icon_discover", "label": "Discover", "body": "Understand the brief"},
                    {"icon_asset_id": "icon_design",   "label": "Design",   "body": "Shape the outline"},
                    {"icon_asset_id": "icon_build",    "label": "Build",    "body": "Compose the deck"},
                    {"icon_asset_id": "icon_ship",     "label": "Ship",     "body": "Render the .pptx"},
                ],
            },
        },
    },
    "six_up": {
        "content": {
            "title": "str (optional)",
            "items": "list[{icon_asset_id?, label, body}] (6)",
        },
        "params": {},
        "use_when": "6 cells in a 3x2 grid each with icon, label, body. For numbered emphasis use numbered_list_6up.",
        "example": {
            "content": {
                "title": "Capabilities",
                "items": [
                    {"icon_asset_id": "icon_search",   "label": "Discovery",   "body": "Brief + outline"},
                    {"icon_asset_id": "icon_layout",   "label": "Composition", "body": "26 recipes"},
                    {"icon_asset_id": "icon_grid",     "label": "Layout",      "body": "12×12 grid"},
                    {"icon_asset_id": "icon_check",    "label": "Validation",  "body": "8 critics"},
                    {"icon_asset_id": "icon_image",    "label": "Assets",      "body": "SVG + raster"},
                    {"icon_asset_id": "icon_render",   "label": "Render",      "body": "One-shot .pptx"},
                ],
            },
        },
    },
    "matrix_2x2": {
        "content": {
            "title": "str (optional)",
            "items": "list[{image, label, body?}] (4)",
        },
        "params": {},
        "use_when": "Generic 2x2 grid of image+text cards. team_grid_2x2 is the team-specific specialization.",
        "example": {
            "content": {
                "title": "Quadrants",
                "items": [
                    {"image": {"asset_id": "photo_office"},  "label": "Office",  "body": "Footprint up 40%"},
                    {"image": {"asset_id": "photo_floor"},   "label": "Floor",   "body": "Open-plan rebuild"},
                    {"image": {"asset_id": "photo_atrium"},  "label": "Atrium",  "body": "Town-hall venue"},
                    {"image": {"asset_id": "photo_roof"},    "label": "Roof",    "body": "Garden + cafe"},
                ],
            },
        },
    },
    "two_card_row": {
        "content": {
            "title": "str (optional)",
            "items": "list[{image, label, body?}] (2)",
        },
        "params": {},
        "use_when": "2 image-and-text cards side-by-side. Matches the source 'Two Content with Two Images' layout.",
        "example": {
            "content": {
                "title": "Two flagship clients",
                "items": [
                    {"image": {"asset_id": "client_acme"},  "label": "Acme Corp",  "body": "Migrated 12 ETL pipelines"},
                    {"image": {"asset_id": "client_globex"},"label": "Globex",    "body": "Replaced legacy BI stack"},
                ],
            },
        },
    },
    "three_card_row": {
        "content": {
            "title": "str (optional)",
            "items": "list[{image, label, body?}] (3)",
        },
        "params": {},
        "use_when": "3 image-and-text cards side-by-side. Like three_up but with full images instead of small icons.",
        "example": {
            "content": {
                "title": "Three offices",
                "items": [
                    {"image": {"asset_id": "office_ldn"}, "label": "London",  "body": "HQ, 200 seats"},
                    {"image": {"asset_id": "office_nyc"}, "label": "New York", "body": "Sales hub, 80 seats"},
                    {"image": {"asset_id": "office_sin"}, "label": "Singapore","body": "APAC, 40 seats"},
                ],
            },
        },
    },
    "chart_full": {
        "content": {
            "title": "str (required)",
            "chart": "chart content (required)",
            "caption": "str (optional, footer attribution)",
        },
        "params": {},
        "use_when": "One chart deserves the full slide width. For chart + commentary sidebar use chart_with_takeaway.",
        "example": {
            "content": {
                "title": "Revenue trend, last 8 quarters",
                "chart": {
                    "type": "line",
                    "categories": ["Q1'24", "Q2'24", "Q3'24", "Q4'24",
                                   "Q1'25", "Q2'25", "Q3'25", "Q4'25"],
                    "series": [{"name": "Revenue",
                                "values": [0.9, 1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8]}],
                },
                "caption": "Source: audited financials.",
            },
        },
    },
}
