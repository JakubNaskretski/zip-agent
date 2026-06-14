# vendor/ — third-party code vendored into zip-agent

Code here is **copied in verbatim** from another repository at a pinned commit,
not authored in this repo. It ships inside `memory.zip` so the agent can run it
in its sandbox (no network, no `pip install` from PyPI).

## graphbuilder/

The metadata-graph engine — the parsing backend for the Salesforce digest
(`librarian/digest/graphbuilder.py`) and the Mule digest
(`librarian/digest/mule.py`). Pure Python, **stdlib-only at runtime by default** (regex Apex
backend). The optional tree-sitter AST backend is not part of this vendored
package — its wheels ship separately in the memory.zip wheelhouse
(``reference/wheelhouse/``, packed by ``scripts/build_memory.py``); when the
boot-time install succeeds, the engine auto-upgrades, accepting both
tree-sitter binding generations via its compatibility shim.

| | |
|---|---|
| Source | `graph-builder` (private sibling repo) |
| Pinned commit | `bfd3907` (engine `main`) |
| Pinned tip | pptx extractor — slides/notes/charts, p14 declared sections |
| Vendored on | 2026-06-13 |
| Scope | the `graphbuilder/` package only — no tests, no scripts |
| Coverage | 26 Salesforce extractors (objects/fields, Apex+methods, triggers, flows+elements, LWC, Aura, Visualforce, flexipages, layouts, perm sets/profiles/groups, sharing rules, approval processes, reports, rules, OmniStudio, labels, …) + the Mule extractors (`graphbuilder/mulesoft` + `extractors/{mule,raml,muleprops,mulebuild}`: flow/flow-ref/connector graph, APIkit surface incl. RAML specs/resources, source triggers, global configs, property files/keys, pom + descriptor build metadata). Confluence + Jira subpackages are included for the planned Phase-4 collectors. Office documents: docx (Word), xlsx/xlsm (Excel), pdf (optional pypdf), pptx/pptm (PowerPoint — slides, speaker notes, chart labels, p14 declared sections). |

The pin is also recorded in code (`librarian/digest/graphbuilder.py:_VENDORED_SHA`)
and at runtime in the built-in KU `agent:tool/graphbuilder`.

### Re-vendoring (updating to a newer engine)

From a checkout of the `graph-builder` repo at the new target commit, run from
the **zip-agent repo root**:

```sh
rm -rf vendor/graphbuilder
git -C <path-to-graph-builder> archive <NEW_SHA> graphbuilder | tar -x -C vendor/
```

Then:

1. Update `_VENDORED_SHA` / `_VENDORED_AT` in `librarian/digest/graphbuilder.py`
   and the pin in this file.
2. Re-check the vocabulary mapping — the adapter's query helpers and the
   `field_of`/`field_type` reconciliation noted in
   `librarian/digest/graphbuilder.py` — against the new `graphbuilder/model.py`
   (`NODE_TYPES` / `EDGE_TYPES`) and the SF extractors; if edge directions / node
   attrs changed, update the helpers.
3. Run the suite: `uv run --python 3.12 --extra dev python -m pytest`.

The vendored tree is checked in deliberately: the agent's only durable store is
`memory.zip`, so every dependency it needs must travel inside it.

## pptx_draft/

The **pptx-grid-skill** — a recipe-driven, 12×12-grid PowerPoint composer for LLM
agents, vendored as the engine behind the on-demand `pptx-draft` skill
(`librarian/skills/pptx_draft.py`). The skill drives the bundle's two scripts as
subprocesses (run with `cwd=<bundle>`): `reader.py` (read-only — theme / the 26
recipes / the toolbelt critics / `validate-slide` / `validate-plan`; needs only
PyYAML) and `render.py` (composes the `.pptx`; needs python-pptx). At build time
`scripts/build_memory.py` copies this whole tree verbatim to the ZIP root as
`pptx/` (its scripts find their sibling modules + the default `theme.yaml` +
`assets/` from `cwd`). This is the *authoring* half of the pptx story (knowledge →
deck DRAFT) — distinct from the office digest (deck → knowledge).

| | |
|---|---|
| Source | `pptx-skill-grid` (public) — the `skill/` tree |
| Pinned commit | `cf6388b` |
| Vendored on | 2026-06-14 |
| Scope | the `skill/` tree, MINUS: the per-org `templates/opening-slide.pptx` + asset binaries (gitignored upstream, never committed) |

The pin is also recorded in code (`librarian/skills/pptx_draft.py:_VENDORED_SHA`)
and at runtime in the built-in KU `agent:tool/pptx-draft`.

**Two deliberate edits to the verbatim tree** (re-apply on any re-vendor): the
two brand-named example decks (`examples/example_branded.json`,
`examples/example_showcase.json`) are dropped, and one real company name in a
`recipes/__init__.py` docstring example (the `team_grid_2x2` bio) was scrubbed to a
fictional one (`Ex-fintech`), per the anonymization HARD RULES. `requirements.txt`
keeps `python-pptx + PyYAML + Pillow + cairosvg` but drops `jsonschema` (declared
upstream but imported nowhere; validation is hand-rolled). `cairosvg` (the SVG
renderer) is NOT in the offline `--pptx` wheelhouse — it needs the system Cairo
library (libcairo), which a pip wheel can't supply; where SVG assets are used the
agent runs `pip install cairosvg` at runtime on a Cairo-capable host (it has network).

Runtime deps are **not** vendored: read/validate verbs need PyYAML and `render.py`
needs python-pptx (+ lxml / XlsxWriter); Pillow lets `render` splice raster assets.
Bundle them offline for the sandbox with `scripts/build_memory.py --pptx`.

### Images, icons, and rebranding

The default flow ships **no image assets** — every picture is a labeled grey-box
placeholder the human pastes in. To supply icons/images, drop binaries + a sidecar
`.yaml` (same stem) into the bundle's `assets/` and reference the `asset_id` in the
plan; `render.py` then splices them — raster (png/jpg) via Pillow, SVG via
`cairosvg` (install it first: `pip install cairosvg`; it is not in the offline
wheelhouse — needs system Cairo). To **rebrand**, this is a
recalibrate-the-file model (no per-deck ingest): edit `theme.yaml` (palette by name
+ fonts + type scale), set the org name in `render.py`'s ORG settings, and supply a
per-org `templates/opening-slide.pptx` — none of which should be committed to this
public repo with real client identity.

### Re-vendoring (updating to a newer pptx-grid-skill)

From a checkout of the `pptx-skill-grid` repo at the new commit, run from the
**zip-agent repo root**:

```sh
rm -rf vendor/pptx_draft
git -C <path-to-pptx-skill-grid> archive <NEW_SHA> skill | tar -x -C vendor/pptx_draft --strip-components=1
```

Then re-apply the two edits above (drop the brand example decks; re-scrub the
`team_grid_2x2` docstring bio in `recipes/__init__.py` to `Ex-fintech`; trim
`requirements.txt`), update `_VENDORED_SHA` / `_VENDORED_AT` in
`librarian/skills/pptx_draft.py` and the pin in this file, and run the suite.
