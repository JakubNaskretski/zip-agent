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

The **pptx-skill v5 consumer** — the read-side of the presentation library:
`reader.py` (the catalog / validate / compose CLI) + `SKILL.md` (the agent
contract) + `requirements.txt`. This is the *authoring* half of the pptx story
(knowledge → deck DRAFT) — the inverse of the office digest (deck → knowledge).
The on-demand skill `librarian/skills/pptx_draft.py` drives `reader.py` as a
subprocess; at build time `scripts/build_memory.py` assembles it beside a
template's data (from `reference/pptx-templates/<name>/`) into a self-contained
`pptx/` bundle at the ZIP root — reader.py locates its bundle from its own path,
so the code and the template data must share one directory.

| | |
|---|---|
| Source | `pptx-skill` (public) — `consumer/reader.py` + `consumer/SKILL_v5.md` |
| Pinned commit | `536c87d` (branch `chore/v5-tidy`) |
| Vendored on | 2026-06-14 |
| Scope | the v5 CONSUMER only (`reader.py` + `SKILL.md` + `requirements.txt`) — no authoring app, no template content |

The pin is also recorded in code (`librarian/skills/pptx_draft.py:_VENDORED_SHA`)
and at runtime in the built-in KU `agent:tool/pptx-draft`.

Runtime deps are **not** vendored: the read verbs need PyYAML and `compose-v5`
needs python-pptx (+ lxml / Pillow / XlsxWriter). Bundle them offline for the
sandbox with `scripts/build_memory.py --pptx` (the same wheelhouse path as
`--ast`); without them the engine and the rest of the agent are unaffected.

### Re-vendoring (updating to a newer pptx-skill consumer)

From a checkout of the `pptx-skill` repo at the new commit, run from the
**zip-agent repo root**:

```sh
cp <pptx-skill>/consumer/reader.py        vendor/pptx_draft/reader.py
cp <pptx-skill>/consumer/SKILL_v5.md      vendor/pptx_draft/SKILL.md
cp <pptx-skill>/consumer/requirements.txt vendor/pptx_draft/requirements.txt
```

Then update `_VENDORED_SHA` / `_VENDORED_AT` in
`librarian/skills/pptx_draft.py` and the pin in this file, and run the suite.
