# Status & Handoff

**Single page: where the build is, how to resume, and what's left.** Pairs with the build order in [`03_ARCHITECTURE.md`](03_ARCHITECTURE.md) §11.

> Test data (any Salesforce/Mule exports, scraper dumps) lives under `samples/` and is **gitignored** — it is never committed. All examples below use a placeholder `samples/sf/<org>/` path for whatever local export you point the digest at.

---

## Where we are

| Phase | Status | What exists |
|------:|--------|-------------|
| 1 — Librarian core | ✅ | `librarian/{schema,manifest,changelog,session,store,librarian}.py` — transactional engine, invariants I1–I6/I8/I9/I11/I12/I13, atomic ZIP swap |
| 2 — Bootstrap + master prompt | ✅ | `librarian/bootstrap.py` (`boot()` + auto-checkpoint `Session`), `MASTER_PROMPT.md` (outside the ZIP), `scripts/build_memory.py` |
| 3 — Salesforce digest | ✅ | `librarian/digest/salesforce.py` (+ `omnistudio.py`) — objects/Apex/triggers/flows/LWC/flexipages/permsets/profiles/permset groups → KUs + typed graph; external stub nodes for standard/packaged objects; queries incl. `grants_on`/`pages_for`/`neighbors`/`dependents`. **OmniStudio (OmniScript/IP/DataMapper/FlexCard)** — real standard metadata format (`*.os/oip/rpt/ouc-meta.xml` + embedded JSON) confirmed against a trial org + Vlocity DataPacks; element-level reference keys still provisional (need a populated org). Validated on a sample org export (200 KUs, 269-node graph). |
| 3b — Mule digest | ✅ | `librarian/digest/mule.py` — one KU per Mule config file; flow/sub-flow/connector graph; `flow-ref` calls, connector `uses`, cross-file links; queries `who_calls`/`calls_from`/`connectors_used`/`flows_using`/`search_flows`. Synthetic-tested (no real Mule app yet). |
| 5 — Entity bridge + retrieve | ✅ | `librarian/index.py` (entity bridge + FTS5 search index as a serialized SQLite KU, `rebuild_indexes()`) + `librarian/retrieve.py` (`find_entity`, `cross_source`, `search`, `entity_like`). Source-agnostic; validated on the sample org (341 entity links, 199 FTS docs). |

**Tests:** 54 passing (`.venv/bin/pytest`). Sample org data in `samples/` (gitignored).

---

## How to resume

```bash
cd <repo>
.venv/bin/python -m pytest                      # 46 should pass
# re-run the SF digest against a local export:
.venv/bin/python - <<'PY'
import tempfile
from librarian import Librarian, Store
from librarian.digest import salesforce as sf
lib = Librarian(Store(tempfile.mkdtemp()))
rep, d = sf.ingest_salesforce(lib, "samples/sf/<org>/force-app", "dev", "re-ingest sample")
print(rep.committed_generation, len(d.graph["nodes"]), "nodes")
PY
```

---

## TODO — picked up in priority order

### Next (no new inputs needed)
1. ~~**Entity bridge**~~ — ✅ done. `librarian/index.py` builds a source-agnostic search index (entity bridge + FTS5) as one serialized SQLite KU; `rebuild_indexes()`.
2. ~~**Retrieve / ASK**~~ — ✅ done. `librarian/retrieve.py`: `find_entity`, `cross_source`, `search` (BM25 + snippets), `entity_like`. Compose with the SF graph queries for answers. *(LLM query-rewrite is the agent's runtime job; embeddings still deferred.)*
3. ~~**Wire ASK into the master prompt**~~ — ✅ done. `MASTER_PROMPT.md` §4.1 documents the routing (classify → entity bridge / graph / FTS → expand minimally → cite KU ids + confidence), with a call table; verified every documented call runs against the sample org.

### Soon
3. ~~**Mule digest**~~ — ✅ done (`librarian/digest/mule.py`): flows/sub-flows/connectors graph, `flow-ref` calls, cross-file links, entity-bridge join; queries `who_calls`/`calls_from`/`connectors_used`/`flows_using`/`search_flows`. Synthetic-tested; *validate against a real Mule app when you have one.*
4. **Validate OmniStudio reference extraction** — the parser now matches the **real standard metadata format** (`*.os/oip/rpt/ouc-meta.xml` + embedded JSON in `<propertySetConfig>`/`<dataSourceConfig>`), confirmed against a real trial org. But that org has **no populated** scripts/IPs/Data Mappers (one empty FlexCard), so element-level `REF_KEYS` are still unconfirmed. *Input needed: a few real components (build/load some, or a sanitized export).* Then verify `REF_KEYS`.

### Later
5. **Phase 4 — Jira/Confluence** — `tools/scraper/` (read-only, recursive-from-root, attachment-text-only) + `digest/{jira,confluence}.py` + `reference/pl_lemmas.sqlite` + FTS. *Input needed: Atlassian access for the user to run the scraper locally.*
6. **Phase 6 — MUnit / Apex test generation** — generate against parsed Apex.
7. **Phase 7 — Domain port** — lift the existing JSON KB into `kb/domain/` + `domain.sqlite`.
8. **Curated layer** — `plan_reorganization()` high-level planner on top of the existing atomic Librarian ops; `needs-review` surfacing in the ASK path.
9. **Fill invariant stubs** — I7 (reject hand-edits of derived files) and I10 (KU size caps / ZIP-size guardrails / large-blob sharding) once indexes exist.
10. **Multi-ZIP sharding** — only if the corpus outgrows one ZIP (currently single-ZIP assumed; fallback designed, not built).

---

## Inputs needed from the user (when relevant)
- A small **Mule repo** sample → unblocks the Mule digest.
- A **sanitized OmniStudio export** (a few IPs/OmniScripts/Data Mappers) → validates the provisional OmniStudio parser.
- **Atlassian** access (the user runs the scraper locally) → unblocks Jira/Confluence.
- Real **top queries** (the eval set, `02_NEXT_STEPS.md` A1) → calibrates retrieve and lemmatization.

## Notes
- **Confidentiality:** never commit org data or org/client identifiers. `samples/` and `*.zip` are gitignored; verify staged files before every commit. Test fixtures use synthetic, generic names.
- **Org safety:** only ever run `sf` against the one designated test org; all other orgs (production/client) are strictly off-limits — never list, retrieve, or reference them. (Specifics are kept in local session memory, not in this repo.)
- **Credentials never enter chat** — local `sf`/scraper auth + read-only exports only.
