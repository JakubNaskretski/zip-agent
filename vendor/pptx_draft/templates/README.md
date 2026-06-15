# skill/templates/

Holds pre-rendered .pptx slides that get prepended to agent-generated decks.

## opening-slide.pptx

A single-slide .pptx containing your standard branded opening slide. Drop
it here and `render.py` will use it as slide 1 of every deck (unless the
user opts out for a particular deck).

### How to make one

1. Open PowerPoint or Keynote.
2. Design your opener — title, subtitle, branded background, photo,
   whatever. Make it look exactly the way you want every deck to open.
3. Save as a .pptx **with that one slide only**. If you have multiple
   slides, only the first is used and the rest are ignored.
4. Set the canvas size to 16:9 widescreen (13.333" × 7.5" / 33.867cm ×
   19.05cm) so it matches the agent's content slides. Other aspect
   ratios will display fine on their own slide but the rest of the deck
   will look mismatched.
5. Save here: `skill/templates/opening-slide.pptx`.

### Behavior

- File is **gitignored** — your design stays private. Each machine /
  org keeps its own.
- If the file is missing or `USE_TEMPLATE_OPENER = False` in
  `render.py`, the renderer falls back to having the agent compose a
  cover slide as slide 1.
- The agent calls `python reader.py opener-template-status` during
  Phase 2 outline review. If the template exists, the agent asks the
  user whether to use it or design a custom slide 1.

### What the renderer does

```python
# In render.py:
if USE_TEMPLATE_OPENER and template_path.exists() and plan.get("use_template_opener", True):
    prs = Presentation(str(template_path))   # template's slide is now slide 1
else:
    prs = Presentation()                      # empty
# Agent's slides get appended after.
```

Fonts, gradients, photos, effects — everything python-pptx normally
struggles to reproduce — comes through pixel-perfect because the slide
XML is loaded verbatim from your file.
