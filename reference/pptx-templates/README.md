# pptx-templates — deck styles for the pptx-draft skill

Each subfolder here is one **deck template** the on-demand `pptx-draft` skill can
draft against. They are plain files on purpose: a template is meant to be
**swapped, edited, and versioned by hand** — no authoring app or rebuild of the
engine required to change one.

```
reference/pptx-templates/
  acme/                     # the fictional starter (shipped by default)
    index.json              # catalog: themes + skeletons + assets
    themes/<id>/theme.yaml   # palette + fonts
    themes/<id>/master.pptx  # the host master — brand bars, fonts, page numbers
    skeletons/<id>/skeleton.yaml   # one slide layout: typed slots + constraints
    tag_vocab.yaml
    brand.md                # optional org style rules the agent reads
  <your-template>/          # drop another folder here and it's a new choice
```

A `build_memory.py` build assembles the chosen folder + the vendored
`reader.py`/`SKILL.md` into a self-contained `pptx/` bundle at the ZIP root.

## Use your own deck style

The durable, upgrade-safe path (your edits live in source and survive engine
upgrades):

1. **Make the template folder** from a real `.pptx` using the pptx-skill
   authoring CLI (a separate repo) — it strips a deck into themes + skeletons:
   ```sh
   python authoring/cli.py ingest your-deck.pptx
   python authoring/cli.py build-v5 --out skill-v5.zip
   ```
   Unzip `skill-v5.zip` and copy `index.json`, `themes/`, `skeletons/`,
   `assets/`, `tag_vocab.yaml`, `brand.md` into a new folder here, e.g.
   `reference/pptx-templates/ourbrand/`. (Drag-and-drop in a file manager is
   fine — these are just text + one `master.pptx` per theme.)
2. **Build with it**:
   ```sh
   python scripts/build_memory.py --profile rfp --pptx --pptx-template ourbrand
   ```
   (`--pptx` bundles the python-pptx render wheels; omit `--pptx-template` to use
   the `acme` starter.)

## Hand-edit without re-ingesting

Everything except `master.pptx` is plain text you can edit in any editor:

- **Restyle the brand** — open `themes/<id>/master.pptx` in PowerPoint/Keynote,
  change colours/fonts/logos, save. (Keep the layouts intact; compose clones
  them.)
- **Tweak palette/fonts the skill reports** — edit `themes/<id>/theme.yaml`.
- **Adjust a slide layout** — edit `skeletons/<id>/skeleton.yaml` (slot
  `constraints` like `max_chars` / `max_items`, geometry, categories).
- **Set house style** — edit `brand.md` (the agent reads it before drafting).

Then rebuild. No anonymization shortcut: a real client's `master.pptx`, logo, or
brand text must NOT be committed to this public repo — keep org-specific
templates outside it (e.g. a gitignored path) and point `--pptx-template` at a
folder you copy in at build time.

## Editing a deployed memory.zip directly

`memory.zip` is an ordinary ZIP — a file manager can open it. For a quick,
one-off swap on an already-deployed agent you can replace files in place under
`pptx/` (e.g. drop in a new `pptx/themes/<id>/master.pptx`). Re-upload the zip.
This is convenient but not the source of truth: a later engine upgrade via
`scripts/upgrade_memory.py` rebuilds the code side (including `pptx/`) from the
new build while carrying your `kb/**` knowledge across, so any in-zip template
edit is overwritten on upgrade. For changes you want to keep, edit the template
folder here and rebuild.

## Finishing a drafted deck (for the human)

A deck the agent produces (`draft.pptx`) is a **first draft you complete** — the
agent never inserts images and may leave figures unconfirmed. To finish it:

1. **Open `draft.pptx` in PowerPoint.**
2. **Paste your images.** Every dashed grey rectangle is an image to add by
   hand — click it, delete it, and Insert/paste the real picture. Its italic
   label says what goes there (e.g. *"POC screenshot: order-capture flow"*); a
   bare box reads *"image needed: &lt;slot&gt;"*.
3. **Fill the `<TBC: …>` text.** Use Find/Replace on `<TBC:` to jump to every
   unconfirmed figure or number and replace it with the real value.
4. **Work the checklist.** `draft.pptx.warnings.json` lists every placeholder
   slot (`image_placeholder`) and any text the build had to shrink — use it as
   a punch-list before the deck goes out.

The agent's chat reply should already list the placeholders and `<TBC:` items
inline; this is the same checklist, captured beside the file.
