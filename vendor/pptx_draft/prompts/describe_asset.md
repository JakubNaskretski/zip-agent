# Describe a visual asset

You are filling in a sidecar YAML for a single image asset. A downstream
agent **with no vision** will pick assets to place in slides based purely
on what you write here.

## Input

You will be given an image. Mechanical metadata (width, height, aspect,
dominant colors) is computed automatically — never your job.

## Output

Return YAML in exactly this shape, nothing else:

```yaml
kind: ""              # see "Kind"
tags: []              # 1-4 from the vocabulary below
description: ""       # one neutral sentence, under 25 words
```

Edit the existing sidecar file in place; preserve auto-computed fields
(id, file, width, height, aspect, colors_hex).

## Kind

Pick one:

- `photo` — photographic content
- `icon` — small symbolic mark
- `logo` — brand mark / wordmark
- `illustration` — drawn or rendered (not photographic, not iconic)
- `screenshot` — UI capture, raster-exported diagram
- `vector` — SVG / EMF

## Tags

Pick **1 to 4** tags from the workspace tag vocabulary (see
`asset_tag_vocab.yaml`). Tags describe what is **literally pictured**,
not the rhetorical meaning of a slide the asset was on.

Examples:
- A photo of three people at a whiteboard → `[people, office, meeting]`
- An icon of a magnifying glass → `[abstract]` (no concrete content)
- A chart screenshot → `[chart, data]`
- An empty boardroom → `[office]`

If nothing in the vocab fits, return `tags: []` and add a tag to
`asset_tag_vocab.yaml` first.

## Description

One short, neutral sentence saying what is literally visible.

- Bad: "A team celebrating a successful Q4 launch."
- Good: "Four people standing around a laptop in an open office."

Limits: under 25 words. No marketing language. No interpretation of
meaning. State what is there.

## Example

For a photo of a person tending plants in a greenhouse:

```yaml
kind: photo
tags:
  - people
  - workplace
  - nature
description: "One person in a greenhouse handling rows of small plants under
overhead lighting."
```
