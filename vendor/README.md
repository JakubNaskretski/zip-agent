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
| Pinned commit | `d00a3f1` (engine `main`) |
| Pinned tip | translated display labels (`label_<locale>` attrs via partial donor nodes) + node→source `source_path` traceability, flexipage fields/actions/related-lists/page attrs, Jira envelope incl. releases/sprints/epic, Apex method signatures, Confluence ancestors/timestamps + dual-API tree-sitter shim |
| Vendored on | 2026-06-12 |
| Scope | the `graphbuilder/` package only — no tests, no scripts |
| Coverage | 26 Salesforce extractors (objects/fields, Apex+methods, triggers, flows+elements, LWC, Aura, Visualforce, flexipages, layouts, perm sets/profiles/groups, sharing rules, approval processes, reports, rules, OmniStudio, labels, …) + the Mule extractors (`graphbuilder/mulesoft` + `extractors/{mule,raml,muleprops,mulebuild}`: flow/flow-ref/connector graph, APIkit surface incl. RAML specs/resources, source triggers, global configs, property files/keys, pom + descriptor build metadata). Confluence + Jira subpackages are included for the planned Phase-4 collectors. |

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
