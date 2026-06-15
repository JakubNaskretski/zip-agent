# Examples

> Rendered `.pptx` files are not tracked in git — too much binary churn.
> Re-render any of the JSON plans with `python render.py …` to produce them.

## example_plan.json

An 11-slide Q4-results deck exercising 11 of the 13 recipes:

| # | Recipe | Notes |
|---|---|---|
| 1 | title_only | Cover |
| 2 | section_divider | "01 / Market context" — big orange numeral |
| 3 | title_bullets | Workhorse content slide |
| 4 | chart_with_takeaway | Column chart + sidebar bullets |
| 5 | section_divider | "02 / Our performance" |
| 6 | metric_strip | 4 KPIs with status-colored deltas |
| 7 | comparison | FY24 vs FY25 segment mix |
| 8 | table_full | Pipeline by region |
| 9 | three_up | FY26 priorities (icon placeholders) |
| 10 | quote | Pull-quote |
| 11 | cta_closing | Thank you + Q&A |

## Render it

```bash
python render.py examples/example_plan.json examples/out.pptx
```

`out.pptx` opens directly in PowerPoint / Keynote / LibreOffice.
Image and icon slots render as dashed grey placeholders (no assets supplied).

## With assets

```bash
python describe_assets.py /path/to/assets/
python splice_assets.py examples/out.pptx --assets /path/to/assets -o examples/final.pptx
```

Any placeholder whose `asset_id` matches a sidecar in the asset folder gets
swapped for the binary. Unmatched ones stay placeholders and a
`final.pptx.warnings.txt` lists them.
