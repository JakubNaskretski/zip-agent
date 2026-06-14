# assets/

Binary assets the agent can reference by id. Each binary needs a sidecar
YAML with the same stem describing it.

```
assets/
  hero_team.jpg          ← binary (jpg / png / svg / webp / …)
  hero_team.yaml         ← sidecar with id, kind, dims, tags, description
  product_shot.png
  product_shot.yaml
  pictogram_002571.svg
  pictogram_002571.yaml
  …
```

## How to populate this folder

Drop binaries in, then generate sidecars:

```bash
python describe_assets.py assets/
# Or with vision-prompt scaffolding for tag/description fill-in:
python describe_assets.py assets/ --with-vision-prompts
```

Each sidecar has the shape:

```yaml
id: hero_team
file: hero_team.jpg
kind: photo
width: 1920
height: 1280
aspect: 1.500
colors_hex: ["#2A3F5F", ...]
tags: []            # user/LLM fills — see asset_tag_vocab.yaml
description: ""     # one neutral sentence, ≤25 words
status: pending
```

## What the agent sees

The agent discovers assets via:

```bash
python reader.py find-asset --kind photo --tags people,office
```

If no `<asset_dir>` is passed, the command defaults to **this folder**.
The agent reads only the sidecars (text) — never the binaries — and picks
assets by `kind`, `tags`, and `description`.

## What lives here vs externally

Generic, reusable atoms (icons, pictograms, stock photos that any deck
might use) → in this folder, bundled with the skill.

Deck-specific or sensitive binaries (one-off product shots, customer
logos, internal screenshots) → keep externally and pass `--assets
/path/to/external/` to `render.py` or `splice_assets.py` to override
the default.
