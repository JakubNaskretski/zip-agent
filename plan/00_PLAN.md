# Build Plan — Enterprise Project Agent

**Goal:** a ZIP-distributed agent definition consumed by a code-interpreter sandbox host model, designed to support a single large software-delivery project in the energy sector. The agent answers questions and generates artefacts across four knowledge sources tied to one in-progress project.

This plan is informed by the verification work in `../analysis/` — every architectural decision below points back to a specific issue we found in the analyzed agent and explicitly rejects that approach.

> **⚠️ Partially superseded (2026-05-29).** The current, precise architecture is [`03_ARCHITECTURE.md`](03_ARCHITECTURE.md) — read it first. It changes two framing decisions below:
> 1. **§3 two-codebase / external builder** → the heavy builder shrinks to a strictly **read-only scraper** (Jira/Confluence only); all parsing and indexing is **digested in-sandbox** by the agent itself.
> 2. **Dual-platform / multi-host** assumptions are dropped → the target is a **single code-interpreter host** (model not hardcoded), and the ZIP is a **living memory the agent edits** through the *Librarian* method.
>
> Still fully current in this doc: §1 (what the agent is for), §2 (constraints), §4 (ZIP contents, modulo the layout refinements in 03 §4), §5 (per-source indexing), §6 (anti-patterns), §7 (effort), §8 (scope inputs). Use 03 for the architecture; use this doc for scope, effort, and the anti-pattern catalog.

---

## 1. What the agent is for

Single-tenant, single-project agent. Knowledge spans four sources:

| Source                       | Acquisition                                       | Volatility      | Quality                                                |
|------------------------------|---------------------------------------------------|-----------------|--------------------------------------------------------|
| **Jira** (on-prem)            | Scraped via user-supplied token (external pipeline)| High — daily    | Questionable; tickets are inconsistent, prose is mixed |
| **Confluence** (on-prem)      | Scraped via user-supplied token                    | Medium — weekly | Questionable; pages stale, structure inconsistent       |
| **Mule repository**           | User pastes local repo path                        | Medium          | Code — structured, parseable                            |
| **Salesforce repository**     | User pastes local repo path                        | Medium          | Code + metadata — structured                            |
| **Domain** (public docs)       | Built-in to the agent                              | Low — rare      | High — official regulatory documents                    |

Top-5 expected user questions (these drive everything):

1. "Which Mule flow handles X?" — needs flow-level graph, not retrieval
2. "Generate MUnit tests for this Mule flow" — needs Mule parser + best-practice KB
3. "What does the Confluence page about Y say?" — needs prose retrieval (the hard one)
4. "Which Jira tickets touch the X service?" — needs entity-anchored cross-source join
5. "What's the Apex class structure around Account.Status__c?" — needs SF metadata graph

**What this is not:**
- Not a multi-tenant agent factory
- Not a live-data agent (the sandbox is isolated)
- Not a code-execution agent (it analyzes, doesn't run)

## 2. Hard constraints (verified)

| Constraint                          | Value                                                                                  |
|-------------------------------------|----------------------------------------------------------------------------------------|
| Runtime                             | Code-interpreter sandbox (Python 3.11+, file access at `/mnt/data`)                    |
| Outbound network                    | **None** — fully isolated                                                              |
| Package installation                | `pip install` allowed from a local wheelhouse (~30–60 s cold start)                    |
| Tenancy                             | Single project — no multi-tenant logic                                                 |
| Data acquisition                    | External pipeline (the "builder") with the user's credentials → builds & uploads ZIP   |
| Document language                   | Mostly Polish for Jira/Confluence content; English for code, comments, Domain bilingual |
| Refresh cadence                     | User triggers a rebuild as needed                                                      |
| Embedding model                     | **Not required** at runtime; available as an optional offline-baked index               |

## 3. Two-codebase architecture

```
┌────────────────────────────────────────────────────────────┐
│  BUILDER (external — runs on user's machine or CI)         │
│  ─────────────────────────────────────────────────────     │
│  Unrestricted: network, embedding APIs, GBs of deps        │
│                                                            │
│  Inputs: Atlassian token, Mule repo path, SF repo path     │
│  Pipeline:                                                 │
│    1. Scrape Jira + Confluence → normalise → entity-extract│
│    2. Parse Mule XML → flow graph                          │
│    3. Parse SF metadata + Apex → object/class graph        │
│    4. Lemmatize Polish prose (stanza)                      │
│    5. LLM-generate per-doc synonym dictionary              │
│    6. Build SQLite indexes (FTS5 + entity + graph)         │
│    7. Bundle with framework + Domain → ZIP                  │
│                                                            │
│  Output: AgentDefinition_v{version}.zip                    │
└────────────────────────────────────────────────────────────┘
                          │
                          ▼  user uploads ZIP
┌────────────────────────────────────────────────────────────┐
│  RUNTIME (in the sandbox)                                  │
│  ─────────────────────────────────────────────────────     │
│  Restricted: no network, pip-install once per session      │
│                                                            │
│  Loads SQLite indexes. No re-scraping, no live API calls.  │
│  Tools: retrieve / graph_walk / inspect / generate / summarize │
│  Model orchestrates tool calls based on user query.        │
└────────────────────────────────────────────────────────────┘
```

The strict separation **fixes the analyzed agent's central mistake**: it conflated builder concerns (parsing user-uploaded knowledge) with runtime concerns (answering questions) into one pile of tools. Two codebases, two responsibilities, no shared globals.

## 4. ZIP contents

```
AgentDefinition_v{version}.zip       (~10–50 MB depending on baked indexes)
├── agent_manifest.json              # one schema, one writer, typed
├── MasterPrompt.md
├── agent/                           # framework, ~1.5–2K LOC
│   ├── manifest.py                  # single-writer CRUD
│   ├── retrieve.py                  # FTS5 + entity-anchor + LLM-rewrite
│   ├── graph.py                     # networkx walks over pre-built graphs
│   ├── session.py                   # auto-tracker on disk (not globals)
│   ├── tools.py                     # tool registry
│   └── schema.py                    # naming, validation
├── adapters/
│   ├── mule/                        # parser + best-practices KB + MUnit generator
│   ├── salesforce/                  # SF + Apex parser + best-practices KB
│   ├── atlassian/                   # query layer over pre-built indexes (no scraping)
│   └── domain/                       # ported from existing Domain work
├── kb/                              # indexes + curated content
│   ├── indexes/
│   │   ├── jira.sqlite              # FTS5 + entities + synonym dict
│   │   ├── confluence.sqlite        # same
│   │   ├── mule_graph.json          # serialised networkx
│   │   ├── sf_graph.json
│   │   └── domain/                   # existing Domain KB ported
│   ├── standards/
│   │   ├── mule_best_practices.md
│   │   └── sf_best_practices.md
│   └── overrides.md                 # per-project prompt tweaks
└── README.md                        # how to use, how to rebuild
```

## 5. Indexing strategy (the crux)

Per-source approach — embeddings are **not** required because three of four sources are structured graphs, not prose.

### 5.1 Mule and Salesforce — graphs, not retrieval

Build the graph once in the builder; query at runtime. Edges:
- Mule: `flow → flow` (via `<flow-ref>`), `flow → connector`, `flow → DWL transform`
- SF: `Apex class → Apex method → SObject.field`, `trigger → SObject`, `flow → SObject`

Runtime tools:
```python
who_calls(node_id) -> list[node_id]
what_does_X_call(node_id) -> list[node_id]
fields_of(object_name) -> list[field]
trigger_for(object_name) -> Trigger | None
```

These return exact answers. No retrieval ambiguity, no embedding similarity.

### 5.2 Jira and Confluence — Polish prose retrieval

The hard part. Five layers stacked (see `01_INDEXING.md` for detail):

1. **SQLite FTS5** with `unicode61` tokenizer
2. **Polish lemmatization** at build time (`stanza` or `spacy + pl_core_news_md`) — index stores lemma forms
3. **LLM-generated synonym table** at build time — one entry per doc with Polish/English variants
4. **Entity-anchored exact-match** — JIRA IDs, page titles, named entities → O(1) lookups
5. **Query rewriting at runtime** — agent's LLM paraphrases the query, unions BM25 results

This stack gets ~85% of embedding-quality retrieval for technical content. Add embeddings later as a rerank-only pass if recall is still missing.

### 5.3 Domain — bounded vocabulary, hand-curated routing

Port the existing Domain KB (`Bus_ENRG_KB_Domain_*` from the analyzed agent — 14 JSON files covering messages, datatypes, processes, scenarios, error codes, SWI, IRiESP). The vocabulary is small and explicit (`PL-xxx` codes, `CK-xxx` enums, section IDs) so the existing hand-curated approach actually works — analysis component 01 confirmed this.

## 6. What we explicitly will not repeat from the analyzed agent

| Anti-pattern (see `../analysis/`)                          | Our approach                                                        |
|------------------------------------------------------------|---------------------------------------------------------------------|
| Static hardcoded `_DOMAIN_KEYWORDS` dict                   | Per-doc synonym dict built from content, not from filenames         |
| L0/L1/L2 markdown indexes                                  | SQLite + graphs; no Markdown-as-database                            |
| Three writers to the manifest, three schemas               | One writer (`manifest.py`), one TypedDict-validated schema           |
| `stats.total_resources` stored and drifted                  | Computed view, never persisted                                      |
| Shared-globals `exec()` everywhere                          | Normal `import`. Tools are functions. Pytest works.                  |
| Auto-tracker in module globals                              | On-disk JSON at `kb/dev/_session.json`, flushed on every mutation   |
| `release_version()` bypasses `pre_release_check()`         | One release path. `pre_release_check()` is the gate, no kwargs hack |
| AST parsed 4× per regression run                            | One `PackageView` object; parse once, share                         |
| Verbose `print()` chatter into context                      | Structured logger; reports to disk, summary line to context          |
| Domain IDs renumber across rebuilds                         | Stable IDs from content hash or alphabetical key                    |
| Same Salesforce vocabulary leaked into every project domain | Per-domain vocabularies, no cross-pollution                          |

## 7. Effort estimate

| Phase | Scope                                                                        | Estimate     |
|------:|------------------------------------------------------------------------------|--------------|
| 0     | Scope spec (use cases, quality bar, v1 contents)                              | 1–2 days     |
| 1     | Runtime framework (manifest, schema, tools, session, retrieve, graph)         | 5–7 days     |
| 2     | Mule adapter (parser, graph, best-practices KB, basic MUnit generator)         | 7–10 days    |
| 3     | Salesforce adapter (metadata + Apex AST + graph + best-practices KB)           | 7–10 days    |
| 4     | Atlassian builder (scraper, normalizer, lemmatizer, synonym builder)           | 7–10 days    |
| 5     | Atlassian runtime (FTS5 + entity + query rewriter)                             | 3–5 days     |
| 6     | Domain port (lift existing JSON, reshape to new schema)                          | 2–3 days     |
| 7     | External builder pipeline (CLI, orchestration, ZIP packaging, refresh logic)   | 5–7 days     |
| 8     | Testing, prompt engineering, integration                                        | 5–10 days    |
|       | **Total**                                                                       | **42–64 days** (≈ 8–12 weeks)|

One competent engineer with AI-pair-programming can compress this to ~6 weeks. Two engineers, ~4 weeks.

The MUnit generation quality is the biggest schedule risk — generating *useful* tests is a domain problem, not a framework problem.

## 8. Next concrete deliverables (before code)

1. **Scope spec** — top-5 user questions with example queries and expected answers. Drives the retrieval evaluation set.
2. **One real Jira ticket + one real Confluence page** (or anonymised equivalents) — used to calibrate the Polish lemmatization quality.
3. **Mule + SF sample directory** (small) — used to validate the parser before building it out.
4. **Decision on embeddings** — bake a multilingual MiniLM embedding into the ZIP as an optional rerank layer, or strictly no embeddings? (~150 MB ZIP overhead either way.)
5. **Decision on the builder host** — laptop script? CI job? Both?

These five inputs unblock Phase 1. See `02_NEXT_STEPS.md`.

## 9. Reference docs

- [`01_INDEXING.md`](01_INDEXING.md) — Polish indexing detail; per-source pipelines; BM25 + lemmatization + synonym + entity-anchor stack
- [`02_NEXT_STEPS.md`](02_NEXT_STEPS.md) — open decisions, scope spec template, what to deliver to unblock build
- [`../analysis/`](../analysis/) — verification of the existing agent we studied; every "we won't do X" above traces back to a specific file:line finding there
