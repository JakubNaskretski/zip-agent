# Indexing Strategy

The user's stated #1 concern from analyzing the existing agent: **"long response times and poor mapping and indexing"**. The plan here is what fixes that.

## Why embeddings are not required

Three of four sources are not prose. They are graphs or structured records.

| Source            | Shape                                                | Right tool                                          |
|-------------------|------------------------------------------------------|------------------------------------------------------|
| Mule              | Structured XML (flows, connectors, configs)          | Pre-built graph + exact lookups                      |
| Salesforce        | Structured metadata + Apex AST                       | Pre-built graph + exact lookups                      |
| Domain             | Bounded vocabulary (PL-xxx codes, CK-xxx enums)     | Hand-curated routing + exact lookups (proven)        |
| Jira / Confluence | Free prose, messy, mostly Polish, mixed quality      | FTS5 + lemma + synonym + entity-anchor + LLM rewrite |

Embeddings would only help the fourth source. And for technical content in Polish, with proper lemmatization + LLM query rewriting, BM25 + entity anchors does ~85% of the job — sometimes better than embeddings because it doesn't get fooled by surface similarity.

If recall on Confluence Q&A is still missing at v1, we can bake a multilingual MiniLM (~150 MB) into the ZIP as an offline-precomputed rerank pass. That decision is deferred until we evaluate v1.

## Polish is the actual indexing problem

Polish has seven grammatical cases for nouns and rich verb conjugation. `konto`, `konta`, `kontem`, `koncie`, `kont` all mean "account" but tokenize to five different terms. Without lemmatization, BM25 retrieves ~40% of what it should for Polish queries.

The fix: heavy NLP at build time, simple lookup at runtime.

```
BUILD                                              RUNTIME
─────                                              ───────
Polish doc                                         User query (Polish)
   │                                                  │
   ▼                                                  ▼
stanza or spacy + pl_core_news_md                  light stemmer (lookup table)
   │                                                  │
   ▼                                                  ▼
lemmas (e.g. konto)                                lemmas
   │                                                  │
   ▼                                                  ▼
indexed in SQLite FTS5                             FTS5 search
   │                                                  │
   ▼                                                  ▼
LLM-generates per-doc synonyms                     LLM paraphrases query (5 forms)
(once, offline)                                       │
   │                                                  ▼
   ▼                                                  union BM25 results
synonym table                                         │
                                                      ▼
                                                  rerank by entity overlap
                                                      │
                                                      ▼
                                                  return top-k chunks
```

The "AI" lives at build time where there are no constraints. The runtime is pure SQLite lookups + the agent's own LLM doing query rewriting via tool calls.

## Per-source pipelines

### 5.1 Mule

**Build:**
1. Walk repo for `*.xml` files
2. Parse with `lxml` — extract:
   - `<flow name="…">` → flow node
   - `<sub-flow name="…">` → sub-flow node
   - `<flow-ref name="…"/>` → directed edge
   - `<dw:transform>` body, `<http:listener>`, `<db:select>`, etc. — connector/op nodes attached to their containing flow
3. Build a `networkx.DiGraph`
4. Serialise to `kb/indexes/mule_graph.json` (node-link format) — small enough to keep, easy to load

**Runtime tools:**
```python
flow(name)               # → full flow node with all ops
who_calls(flow_name)     # → callers via flow-ref
calls_from(flow_name)    # → downstream flows
connectors_used(flow)    # → http, db, salesforce, etc.
search_flows(keyword)    # → fuzzy match on flow names, no FTS needed
```

**Best-practice KB** ships as a hand-curated `kb/standards/mule_best_practices.md`. The MUnit generator references this KB explicitly.

### 5.2 Salesforce

**Build:**
1. Walk repo for `*.object`, `*.cls`, `*.trigger`, `*.flow-meta.xml`, `*.permissionset-meta.xml`
2. Parse:
   - `.object` XML → object node with field nodes
   - `.cls` via `tree-sitter-apex` → class/method/field nodes with relationships (`extends`, `implements`, method calls within file)
   - `.trigger` → trigger node, bound to object
   - `.flow-meta.xml` → flow node, edges to objects it touches
3. Resolve cross-file references (Apex class A calls class B → edge)
4. Build a `networkx.DiGraph` with typed nodes/edges
5. Serialise to `kb/indexes/sf_graph.json`

**Runtime tools:**
```python
object(name)                  # → SObject node with fields
fields_of(object_name)        # → list of fields
field(object_name, field)     # → field with type, attributes, references
apex_class(name)              # → class node with methods
who_calls(class_or_method)    # → callers
triggers_on(object_name)      # → triggers bound to this object
flows_touching(object_name)   # → declarative flows that reference it
```

**Best-practice KB** as a curated `kb/standards/sf_best_practices.md` — for an early v1 we can lift content from the existing SF dev standards file we found in the analyzed agent (it was a decent baseline; ~46 KB, mostly correct).

### 5.3 Jira / Confluence

**Build (most complex — done in external pipeline):**

1. **Scrape** via REST API with user token:
   - Jira: issues, comments, transitions; persist JSON per issue
   - Confluence: pages + history, persist HTML
2. **Normalise** to a common doc record:
   ```python
   {
     "id": "JIRA-PROJ-123" | "CONF-page-456",
     "source": "jira" | "confluence",
     "title": str,
     "body": str,            # cleaned plaintext, HTML stripped
     "body_polish": str,     # lemmatized form for FTS index
     "entities": [str],      # JIRA IDs, page titles, named entities found in body
     "links": [doc_id],      # cross-references to other docs
     "metadata": {...}
   }
   ```
3. **Lemmatize** Polish body using `stanza` (best quality) or `spacy + pl_core_news_md` (lighter). Cache lemmas — re-lemmatizing is expensive.
4. **Entity-extract** — regex for JIRA IDs (`[A-Z]+-\d+`), title patterns, and a small named-entity list from project glossary
5. **Synonym-build** — for each doc, call Claude/GPT once: "Given this doc, list 10–20 Polish + English terms that someone might search by". Cache per doc-hash.
6. **Index in SQLite**:
   ```sql
   CREATE VIRTUAL TABLE docs USING fts5(
     id, title, body_polish,
     tokenize = 'unicode61'
   );
   CREATE TABLE entities (doc_id, entity, kind);
   CREATE TABLE synonyms (doc_id, term, weight);
   CREATE TABLE links (src_doc_id, dst_doc_id, link_kind);
   ```

**Runtime tools:**
```python
retrieve(query, k=10, sources=None)
    # 1. Tokenize + light Polish stemming (lookup table)
    # 2. LLM-paraphrase query into 5 forms (via the agent's own model)
    # 3. Run FTS5 BM25 on each form, union with weighted dedup
    # 4. Entity-anchor boost: if query mentions JIRA-123 → top-rank that doc
    # 5. Synonym table: query terms → expand → boost docs whose synonyms match
    # 6. Return top-k {doc_id, score, snippet}

get_doc(doc_id) -> Doc        # full doc by ID
search_entities(name)         # exact + fuzzy lookup
linked_docs(doc_id)           # follow the links table
```

### 5.4 Domain

Lift the existing 14 JSON files (Messages, DataTypes, CrossRef, Scenarios, Processes, ProcessDetails, SWI, IRiESP, ErrorCodes, UpdateCategories, PrioMatrix, TSKBMain, Catalog, VerificationRegistry). Reshape into a single SQLite under `kb/indexes/domain.sqlite` for consistency with the other sources. The existing tools (`Bus_ENRG_Tool_Domain_*.py`) can be reused — they were among the few well-designed parts of what we analyzed.

**Runtime tools:**
```python
message(pl_code)              # → UNK message spec
datatype(ck_code)             # → enum or type
process(group, id)            # → process spec
swi_section(id)
iriesp_section(id)
```

## Cross-source queries

The interesting questions cross sources. Examples and resolution:

| Question                                                         | Resolution                                                                                                   |
|------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------|
| "Which Jira tickets touch the `MeterPointService` Mule flow?"     | `MeterPointService` is an entity. Search Jira entity table for that exact string. O(1).                       |
| "Which Confluence page documents the Apex class `AccountUpdater`?"| Apex class names are entities. Confluence entity table lookup.                                                |
| "Generate MUnit for flow `accountUpdateFlow`"                     | Mule graph → flow definition → MUnit generator with best-practice KB injected as system prompt.               |
| "What does Domain say about the message in this Confluence page?"  | Two-step: extract PL-xxx code from Confluence retrieval → Domain `message(pl_code)`.                          |

All of these are exact-match or graph-walk problems, not retrieval problems. The pre-built entity tables + graphs make them O(1) or O(log n). This is what we mean by "much better maps and relationships" — the maps are pre-computed entity bridges, not on-the-fly similarity searches.

## Refresh / incremental indexing

The builder uses a hash-per-doc table to skip unchanged docs:

```python
# In builder
prev_hashes = load(".cache/doc_hashes.json")
for doc in scraped_docs:
    h = hash(doc.body)
    if prev_hashes.get(doc.id) == h:
        skip()  # already indexed
    else:
        lemmatize(); synonym_build(); entity_extract(); write_to_db()
```

The analyzed agent computed `content_hash` per file and **never used it** (RebuildIndexes.py:252 — see `../analysis/components/01_indexing.md`). We will actually use ours. Incremental rebuilds should run in seconds for "I edited 3 Confluence pages" cases, not minutes.

## Quality bar

For v1 we'll define acceptance based on a hand-curated evaluation set:

- **20 representative queries** (4 per source + 4 cross-source) → top-3 retrieved docs include the correct answer ≥ 90% of the time
- **10 Mule flows** → graph correctly identifies all `flow-ref` edges (parser correctness, not retrieval)
- **10 SF classes** → entity graph correctly identifies field references inside Apex (tree-sitter correctness)
- **20 Polish lemmatization spot-checks** → stanza/spacy gets the lemma right ≥ 95%

The eval set itself is the most valuable thing to build before any indexer code. See `02_NEXT_STEPS.md`.
