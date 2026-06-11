# Architecture — Living-Memory Agent

**Status:** the precise, current architecture for the agent. The per-source indexing logic in [`INDEXING.md`](INDEXING.md) is part of it, relocated to run inside the agent's own digest step (in-sandbox) rather than an external builder. Earlier two-codebase / dual-platform framing has been dropped.

This document is the canonical architecture for the agent we are building. It is grounded in our verification of an existing framework we studied — every "we do it this way" points back to a specific failure we documented there.

---

## 0. The reframing in one paragraph

The agent runs on a **single enterprise code-interpreter host** (a large reasoning model with a Python sandbox; we deliberately do **not** hardcode which model — it may be one GPT-5-class model today and a different one tomorrow). It has **no persistent memory except one ZIP** — that ZIP *is* its memory. The agent loads the ZIP at session start, runs Python tools that live inside it, and emits an updated ZIP that becomes the new memory. Data enters manually: Salesforce and Mule **repos** are handed in as ZIPs; Jira and Confluence are **scraped on the user's machine** by a local script the agent provides, then handed back as an export ZIP. The agent **digests everything in-sandbox** and is allowed to **grow and reorganize its own knowledge** — but only through a single governed method (the *Librarian*), because the model cannot be trusted to keep the structure clean by hand. Beyond the raw sources, the agent maintains an **evolving curated layer** (glossary, cross-source mappings, decisions, lessons) that is the "keep the most important things" mechanism.

Four defining choices that shape everything below:

| # | Delta | Consequence |
|---|-------|-------------|
| 1 | **Single host (model-agnostic)** | Drop all dual-platform machinery: `StorageAdapter` monkey-patch, two `MasterPrompt §3` variants, Copilot folder mode. The model itself is not hardcoded anywhere. |
| 2 | **Agent digests in-sandbox** | Parsing + indexing move *into* the agent. Only the network-bound scraper stays external. |
| 3 | **ZIP is a living memory the agent edits** | Need a governed, transactional mutation method — the *Librarian* — as the architectural spine. |
| 4 | **Evolving curated layer** | A third knowledge tier the agent authors itself, with provenance + staleness tracking. |

---

## 1. Hard constraints (updated)

| Constraint | Value |
|------------|-------|
| Host | One enterprise code-interpreter sandbox (Python 3.11+, file access on a working dir that persists across tool calls within a session — *confirmed*). **Model not hardcoded** — likely a GPT-5-class model, but the design assumes nothing about the exact version. |
| Persistence | **The memory ZIP, retained by the host across sessions.** No general filesystem persists, but the agent's one memory ZIP does — it reads it at session start and writes it back. The user does *not* re-upload/download it each time. |
| Outbound network | None in the sandbox. The only networked component is the local scraper on the user's machine. |
| Package install | `pip install` from a local wheelhouse bundled in the ZIP (cold start cost — keep the wheelhouse lean). |
| Tenancy | Single project (energy-sector Salesforce + Mule delivery). No agent-factory, no child agents. |
| Data acquisition | Manual. SF/Mule repos as ZIPs; Jira/Confluence via the provided scraper. |
| Document language | Polish for Jira/Confluence prose; English for code; bilingual Domain. |
| Refresh cadence | User-triggered, incremental. |

The **persistence constraint is the dominant force** in this design. The agent's *only* durable store is its memory ZIP, which the host retains across sessions. Three things follow: (a) every tool the agent needs ships *inside* the ZIP — there is no other place to put code; (b) every mutation must be written through *into* the memory ZIP, so commits persist to the ZIP and the agent snapshots a versioned backup after each significant commit, not only at session end; (c) state that the studied framework kept in Python module globals (`_pending_changes`, `_session_author`) **must** live on disk inside the ZIP instead — that was analysis finding #3 and it is fatal here, not merely smelly. The user is **not** part of the persistence loop: they only hand in **new data** (repos, scraper exports); the memory ZIP itself stays with the agent.

### 1.1 Non-negotiable safety principle — the source systems are read-only, always

> **WE NEVER MODIFY JIRA OR CONFLUENCE.** Connection to them happens *only* through the scraper, and the scraper *only reads*.

This is the highest-priority constraint in the whole system, above performance, above recall, above convenience:

| Rule | Enforcement |
|------|-------------|
| The scraper issues **GET requests only** — no POST/PUT/DELETE/PATCH, ever. | A request-method allowlist in `scrape.py`; any non-GET call raises and aborts. No write code paths exist in the scraper at all. |
| The agent has **no path** to Jira/Confluence — it is network-isolated. | Architectural: the sandbox has no outbound network (§1). The agent literally *cannot* reach the source systems. |
| Recommend a **read-only API token**. | The scraper README instructs the user to mint a least-privilege, read-only token. |
| **No write-back feature** is in scope — not now, not as a "phase 2 maybe." | Removed from the open-questions list entirely. The agent is a consumer of source data, never a producer to it. |

Source data is irreplaceable and shared with other teams; corrupting it is the one failure mode with no rollback. Every other part of the system is designed to be cautious, but this one is designed to be *impossible to violate*.

---

## 2. Memory model

### 2.1 Three tiers + built-ins

Everything in the ZIP is one of:

| Tier | Mutability | What it is | Examples |
|------|------------|------------|----------|
| **raw** | Immutable (re-ingest to change) | Verbatim ingested source records | a Jira issue JSON, a Confluence page, a Mule `.xml`, an Apex `.cls` |
| **structured** | Derived (Librarian-owned) | Machine artifacts computed from raw | Mule flow graph, SF object/class graph, normalized doc records |
| **indexes** | Derived (Librarian-owned) | Search/routing artifacts | FTS5 sqlite, entity bridge, generated routing table |
| **curated** | Agent-authored (governed) | Distilled knowledge the agent grows | glossary, cross-source mappings, decisions, lessons, standards |
| **built-in** | Versioned content | Ships with the framework | Domain KB, best-practice standards, the scraper |

The discipline: **raw is immutable, derived is never hand-edited, curated is the only tier the agent freely authors — and even that goes through the Librarian.** This directly fixes the studied framework's worst habit: hand-editing the L1 index and letting three different writers mutate the manifest (analysis findings #1, #2).

### 2.2 The Knowledge Unit (KU)

The atom of memory. Every file in `kb/` is described by exactly one KU record in the manifest:

```jsonc
{
  "id": "jira:PROJ-123",            // STABLE. Derived from source identity. Never renumbered.
  "kind": "source-record",          // source-record | graph | doc | index | curated-note | standard | tool | instruction
  "tier": "raw",                    // raw | structured | indexes | curated | built-in
  "source": "jira",                 // jira | confluence | mule | salesforce | domain | agent
  "path": "kb/raw/jira/PROJ-123.json",
  "title": "Meter point sync fails on bulk import",
  "entities": ["MeterPointService", "PROJ-123", "AccountUpdater"],  // cross-source anchors
  "links": [                        // typed edges to other KUs
    {"kind": "references", "to": "mule:flow/meterPointSync"},
    {"kind": "derived-from", "to": null}
  ],
  "provenance": {"batch": "ingest-2026-05-29T10:12", "native_id": "PROJ-123"},
  "freshness": {"as_of": "2026-05-28", "ingested": "2026-05-29T10:12"},
  "confidence": "VERIFIED",         // ✅/🟡/🟠/🔴 — reuses analysis confidence model
  "content_hash": "9f2a…",          // ACTUALLY USED for incremental digest (fixes finding: hash computed but never compared)
  "status": "active"                // active | superseded | retired
}
```

Key properties, each tied to a finding:

- **Stable IDs from source identity**, never sequential, never renumbered across rebuilds → fixes the `overlay_prefix` D-number churn (finding: domain IDs renumber).
- **`content_hash` is load-bearing** — the digest compares it to skip unchanged records → fixes "hash computed but never used".
- **`stats` is never a KU field and never stored** — counts are a computed view over the manifest (fixes the 119/93/98/92 four-way drift).
- **Curated KUs must carry `derived-from` links** to the raw KUs they summarize — this is what makes staleness detectable (§9).

### 2.3 KU granularity — what is, and isn't, a KU

The manifest holds one entry per KU, so granularity directly bounds manifest size — and the manifest is rewritten inside *every* transaction (§3.4), so an unbounded manifest is a performance and corruption-window problem. The rule: **a KU is a retrievable, independently-citable unit — not every sub-element.**

| Is a KU (one manifest entry) | Addressable but NOT a manifest KU |
|------------------------------|-----------------------------------|
| a Jira issue (record) | its comments, transitions, links — fields of the issue KU |
| a Confluence page (record) | its comments, labels, attachment-text — part of the page KU |
| a Mule source file (`.xml`) | each flow / sub-flow / connector — **graph nodes** |
| an SF source file (`.cls`/`.object`/`.trigger`) | each class / method / field / SObject — **graph nodes** |
| each derived artifact (a graph file, an index file) | the nodes/rows inside them |
| a curated note | the entities it mentions |

Sub-elements are still fully **indexed and queryable** — the FTS/entity/graph layers see inside the parent KU. A link or entity reference (e.g. `mule:flow/meterPointSync`) resolves to **either a KU id or a graph-node address** via the entity bridge; not every link target is a manifest row. This keeps the manifest in the low thousands of entries even for a large corpus.

---

## 3. The Librarian — the governed mutation method (the spine)

> Your words: the KB reorganization *"needs to be done in some good and conscious manner which the agent cannot always keep, thus usage of a method can be a good idea."* The Librarian **is** that method. It is the **only** sanctioned path that mutates the KB or the manifest. The agent never hand-edits files, never hand-edits the manifest, never hand-edits an index.

### 3.1 The API

```python
# librarian/librarian.py  — a real, importable, pytest-able module. No exec-into-globals.
txn = librarian.begin(author="…", rationale="…")     # rationale required up front

txn.add_ku(ku, body=…)            # stage a new unit
txn.update_ku(ku_id, **changes)   # stage a change to a curated/derived unit (raw is immutable)
txn.retire_ku(ku_id, reason=…)    # mark superseded/retired — NEVER hard-delete
txn.move_ku(ku_id, new_path, new_id=None)   # relocate / re-key (re-points inbound links)

report = txn.preview()            # run ALL validators; return a before/after diff. Show to user.
txn.commit()                      # atomic. Validators run AGAIN. On any failure → rollback, nothing written.
```

A higher-level operation for the "reorganize my knowledge" use case:

```python
plan = librarian.plan_reorganization(goal="merge the two glossary fragments about metering")
# -> decomposes into a list of atomic add/move/retire ops, each validated
report = plan.preview()           # human-readable before/after, shown to the user
plan.commit()                     # only after explicit user confirmation
```

`reorganize` never does anything the atomic ops can't — it just composes them so the agent can express intent at a higher level while the **invariants still run on every underlying op**. This is the "conscious manner encoded in code": the agent supplies intent, the Librarian supplies discipline.

### 3.2 The invariants (what `commit()` enforces, or rejects)

| # | Invariant | Fixes finding |
|---|-----------|---------------|
| I1 | **Single writer.** Only the Librarian writes `manifest.json`. | 3 writers / 3 schemas |
| I2 | **`stats` is computed on read, never persisted.** | 4-way count drift |
| I3 | **Stable IDs.** An ID maps permanently to one source entity (or curated note); never renumbered, never reassigned to a *different* thing. The same source entity always resolves to the same ID — including across a retire→revive (a deleted-then-reappearing Jira ticket reuses its own ID, with `needs-review` re-triggered on dependents). `move_ku` re-keys explicitly and re-points links. | ID renumbering |
| I4 | **Schema valid.** `kind`/`tier`/`source`/naming validated by one validator (`schema.py`), one regex, no second copy. | 3 naming-regex copies |
| I5 | **Changelog gate.** Every commit appends a changelog entry with a **non-empty rationale**; commit reads the staged ops and refuses if the rationale is empty/vague. The gate is *inside* the only write path — it cannot be bypassed. | `release_version` bypasses `pre_release_check` |
| I6 | **No orphan links.** Retiring/moving a KU re-points or flags every inbound link. | (new — prevents rot) |
| I7 | **Derived is rebuilt, never edited.** Indexes/graphs are regenerated by the Librarian from raw+structured; a direct edit to a derived file is rejected. | hand-edited L1 index |
| I8 | **Raw is immutable.** `update_ku` on a raw KU is rejected — you re-ingest. | (new — provenance integrity) |
| I9 | **Idempotent.** Re-staging a KU whose `content_hash` is unchanged is a no-op. | hash never compared |
| I10 | **Budget guardrails.** KU size caps; ZIP-size warning thresholds; large blobs (>N MB) auto-sharded. | 20 MB `Messages.json`, 4.5 MB CrossRef |
| I11 | **On-disk session state.** Author/rationale/pending ops live in `dev/session_state.json`, flushed on every call — not in module globals. | globals evaporate on restart |
| I12 | **Durable, atomic commit.** The new memory ZIP is written to a temp path, fsynced, then atomically renamed over the live ZIP. A crash mid-write leaves the previous ZIP fully intact. | (new — single-ZIP is the only brain) |
| I13 | **Recoverable core.** Indexes + graphs are 100% derived from `raw` + `curated` and can be fully rebuilt anytime; only `raw` + `curated` are irreplaceable state. | (new — makes incremental rebuild safe) |

If any invariant fails in `commit()`, the transaction rolls back and **nothing** is written — the ZIP is never left half-mutated. This is the single most important property in the whole design, because a half-written memory ZIP is a corrupted brain.

### 3.3 Why this is a method, not a guideline

The studied framework *documented* all the right rules (MasterPrompt §10 "NON-NEGOTIABLE") and then shipped code that didn't enforce them — the release path validated its own kwargs and never consulted the tracker. The lesson is: **a rule the LLM is asked to follow is a rule that will drift; a rule the only write-path enforces in code cannot.** The Librarian is the architectural expression of that lesson.

### 3.4 Durability — how a commit physically lands

The memory ZIP is the only brain, so the *physical* write must be as safe as the logical validation. Validation atomicity (§3.2) protects against bad data; this protects against a bad moment (a crash, a killed sandbox) mid-write:

- **Staging is ephemeral; commit is durable.** `begin/add/update/retire` touch only an in-session working tree + `dev/session_state.json`. Nothing is part of memory until `commit()`. If the session dies before commit, the staging is simply gone and memory is untouched.
- **Atomic swap (I12).** `commit()` validates → rebuilds affected derived artifacts → writes a *new* ZIP to `memory.zip.tmp` → fsync → `os.replace()` over `memory.zip`. `os.replace` is atomic within a filesystem, so an interrupted commit leaves the prior ZIP whole. There is never a half-written brain.
- **Recoverable core (I13).** Because indexes and graphs are 100% derived, `rebuild_all()` can regenerate every derived artifact from `kb/raw/` + `kb/curated/`. A manifest-vs-index *generation counter* detects drift (an aborted older build, corruption); on mismatch the agent rebuilds rather than trusting stale indexes. This is also what makes the *scoped* incremental rebuild (§6) safe: if the scoped path is ever wrong, the full rebuild is always correct.
- **Snapshots are diffs, not full copies.** The "backup after every commit" promise is satisfied by the changelog's per-commit diffs (L2) plus at most the last *N* full snapshots — so it does not multiply a large ZIP. A *bad* reorganization is reverted by applying the inverse diff through a normal Librarian transaction, never a hand-edit.

---

## 4. Canonical ZIP layout

The **master prompt lives OUTSIDE the ZIP.** `MASTER_PROMPT.md` (the persona + protocols) is pasted into the agent builder's instructions field — it is a separate deliverable, not bundled in `memory.zip`. The ZIP is the *skill* (engine + knowledge); the master prompt is the *instructions*. The build produces two things: the ZIP, and the pasteable master prompt alongside it.

```
memory.zip                          # the agent's entire persistent memory (the SKILL)
├── manifest.json                   # single source of truth; one writer; stats computed-on-read
│
├── librarian/                      # the engine — REAL python modules, importable, pytest-able
│   ├── librarian.py                # transactional mutation API (§3)
│   ├── manifest.py                 # single-writer manifest CRUD + computed stats view
│   ├── schema.py                   # KU schema + naming/area/type validators (one regex)
│   ├── changelog.py                # changelog append + rationale gate (consulted by commit)
│   ├── index.py                    # incremental (re)build of indexes/graphs
│   ├── retrieve.py                 # runtime query: FTS + entity + graph-walk + LLM query-rewrite
│   ├── graph.py                    # graph build/walk for Mule & SF
│   ├── session.py                  # on-disk session state (NOT globals)
│   ├── bootstrap.py                # open ZIP → return (manifest, files); load engine; ~30 LOC
│   └── digest/                     # ingestion: raw export ZIP → staged KUs
│       ├── detect.py               # identify source type of an incoming data ZIP
│       ├── jira.py / confluence.py # normalize scraped exports → doc KUs
│       ├── mule.py / graphbuilder.py # parse repos → raw KUs + structured graph (SF via vendored vendor/graphbuilder/)
│       └── normalize_pl.py         # light Polish lemmatizer (baked dict + stemmer), used build+query
│
├── kb/                             # the knowledge, by tier (§2.1)
│   ├── raw/{jira,confluence,mule,salesforce}/      # immutable ingested records
│   ├── structured/                 # mule_graph.json, sf_graph.json, normalized docs/
│   ├── indexes/                    # jira.sqlite, confluence.sqlite, domain.sqlite, entities.sqlite, routing.json
│   ├── curated/                    # the EVOLVING layer the agent grows (§9)
│   │   ├── glossary/  mappings/  decisions/  lessons/  standards/
│   └── domain/                      # built-in domain KB (ported from the studied framework)
│
├── tools/scraper/                  # the artifact handed to the user (§8)
│   ├── scrape.py  requirements.txt  README.md  export_schema.json
│
├── reference/                      # baked assets
│   ├── pl_lemmas.sqlite            # Polish lemma lookup table (the in-sandbox NLP, §7.4)
│   └── wheelhouse/                 # pinned wheels for pip-install-at-start
│
└── dev/                            # the agent's self-state — survives because it's in the ZIP
    ├── changelog.json              # one layered changelog, one writer
    ├── session_state.json          # author/rationale/pending ops, flushed every call
    └── verify_history.json
```

Note what is **gone** vs. the studied framework: no `AgentFactory/`, no dual `StorageAdapter`, no `Menu & Config/` JSX, no shared-globals `exec` loader. See §10.

---

## 5. The session loop (how the agent runs)

```
SESSION START
  0. (one-time) MASTER_PROMPT.md is pasted into the agent builder's instructions field
  1. the host already has memory.zip retained (or it is uploaded the first time)
  2. agent: exec bootstrap (the one bootstrap), pip-install from reference/wheelhouse if needed
  3. bootstrap opens the ZIP, reads manifest.json, imports the librarian package
  4. routing table + small indexes are available; large KBs stay on disk, queried in code

DURING SESSION
  - ASK   → retrieve.py (FTS + entity + graph) → synthesize answer with confidence + sources
  - DIGEST→ ingest a data ZIP (§6)
  - GROW  → author/curate KUs via the Librarian (§9)
  - REORG → librarian.plan_reorganization → preview → confirm → commit

SESSION END
  - any committed change is already written through to the memory ZIP, which the host retains
  - the user does NOT download/re-upload — the memory ZIP persists across sessions automatically
  - the agent has recorded a revertible diff after each commit (cheap insurance — see §3.4)
  - on request the agent can hand the user an export of the memory ZIP, but it is not required
```

The safety net is structural: because every commit lands via an atomic swap and leaves the memory ZIP in a valid state (§3.2, §3.4), a crash or mid-session abort never corrupts memory — the worst case is losing the uncommitted staging of the current transaction. A *bad* reorganization is rolled back by applying the inverse changelog diff through a normal Librarian transaction.

---

## 6. Ingestion lifecycle (digest in-sandbox)

```
            user machine                         sandbox (host model)
            ────────────                         ─────────────────
 SF/Mule:   zip the repo subset  ─── upload ──▶  digest.detect → digest.{mule,salesforce}
 Jira/Conf: run provided scraper ─── upload ──▶  digest.detect → digest.{jira,confluence}
                                                       │
                                                       ▼
                                          parse → candidate KUs (raw) + derived (graph/docs)
                                                       │  content_hash dedup vs existing raw
                                                       ▼
                                          librarian.begin → stage adds/updates/retires
                                                       │
                                                       ▼
                                          txn.preview()  →  DIGEST REPORT to user:
                                            "12 new, 3 updated, 40 unchanged, 5 entities added,
                                             1 conflict (PROJ-123 body changed) — confirm?"
                                                       │ user confirms
                                                       ▼
                                          txn.commit()  →  write KUs, manifest, changelog,
                                                            incrementally rebuild affected indexes
                                                       ▼
                                          emit updated memory.zip
```

- **Incremental by `content_hash`** — re-ingesting an unchanged corpus is near-free (fixes the studied framework's full-rehash-every-run).
- **Conflicts surfaced, never silent** — a changed raw record is shown in the digest report; the new content wins (raw is a snapshot of source-of-truth) but the old KU is retired with history preserved, and any curated KU that was `derived-from` it is flagged for review (§9).
- **Absence ≠ deletion.** A raw KU present before but missing from a *scoped* re-ingest is **not** auto-retired — the scope may simply have narrowed (a different JQL, a smaller crawl). Digest marks it `possibly-removed-at-source` and surfaces it for the user to confirm; knowledge is never silently dropped. A true deletion is a deliberate, confirmed retire.
- **Export validated before staging.** Digest checks the export against `export_schema.json` first: schema version understood, `counts` reconcile with `items` length, `skipped[]` surfaced, no truncation. A malformed or partial export is rejected **whole** — it never partially stages (which, combined with §3.4, means a bad export can never reach memory).
- **Index rebuild is scoped, with a full-rebuild floor** — the transaction records which sources/entities it touched; `index.py` rebuilds only those sqlite/graph artifacts. If the scoped path can't guarantee consistency, it falls back to `rebuild_all()` (I13) — correctness over speed.

---

## 7. Indexing (in-sandbox, per source)

The per-source strategy from [`INDEXING.md`](INDEXING.md) holds — it just runs in `digest/` + `index.py` now instead of an external builder. Summary:

| Source | Shape | Mechanism | Runtime query |
|--------|-------|-----------|---------------|
| **Mule** | Structured XML | `lxml` → `networkx` flow graph (`flow-ref` edges, connectors) | `who_calls`, `calls_from`, `flow`, `connectors_used` — exact |
| **Salesforce** | Metadata + Apex | XML + tree-sitter-apex → typed graph | `object`, `fields_of`, `apex_class`, `triggers_on`, `flows_touching` — exact |
| **Domain** | Bounded vocabulary | Built-in; ported JSON → `domain.sqlite` | `message(PL-…)`, `datatype(CK-…)`, `process(...)` — exact |
| **Jira/Confluence** | Polish prose | FTS5 + lemma + entity-anchor + LLM query-rewrite | `retrieve(query, k)`, `get_doc`, `linked_docs` — ranked |
| **(all)** | — | Cross-source **entity bridge** (`entities.sqlite`) | "which Jira tickets touch `MeterPointService`?" → O(1) |

### 7.4 Polish NLP feasibility (the risk you accepted)

You chose **digest-in-sandbox**, accepting that heavy Polish NLP (stanza/spaCy + torch) may be too big for the sandbox. The design handles this without betting on heavy deps:

- **Baked lemma lookup table** (`reference/pl_lemmas.sqlite`): a precomputed inflected-form → base-form dictionary for high-frequency Polish, shipped in the ZIP. Plus a small rule-based suffix stemmer for misses. This is tiny, deterministic, dependency-free, and runs identically at **build time** (indexing) and **query time** (normalizing the user's query) — same normalizer both sides, which is what actually matters for recall.
- **LLM query-rewrite at runtime**: the agent's own host model paraphrases the query into a few Polish/English forms; we union the BM25 hits. The "intelligence" is the host model, costing tokens, not a bundled NLP stack.
- **Escape hatch — flex the hybrid boundary**: if v1 recall on Confluence prose is poor, the *local scraper* (which has full network + deps) can optionally run real stanza lemmatization and emit pre-lemmatized fields in the export. Digest just consumes them. This moves the heavy NLP across the boundary **without changing the in-sandbox architecture** — a config flag, not a redesign.

Embeddings remain deferred (per `INDEXING.md` D1): add a baked multilingual MiniLM rerank pass only if the eval set shows a recall gap.

---

## 8. Scraper handshake (the only external component)

The scraper is a **versioned KU** living at `tools/scraper/`, changelog-tracked like everything else — not a script the agent improvises fresh each time (improvised scripts drift; a pinned artifact does not). It is **strictly read-only** — see the non-negotiable safety principle in §1.1.

```
user: "give me the scraper"
agent: extracts tools/scraper/ from the ZIP, hands over scrape.py + requirements.txt + README
       + the current export_schema.json version
user (on their machine): pip install -r requirements.txt
                         export ATLASSIAN_TOKEN=…       # read-only token; never leaves the machine
                         python scrape.py --confluence-root <space-or-page> --jira PROJ --out export.zip
user: uploads export.zip back to the agent
agent: digest.detect recognizes the export → digest.{jira,confluence} → Librarian (§6)
```

### 8.1 Collect as much as possible, recursively from the root

Confluence is not a flat list of pages — it has **multiple component types**, and we want to capture them comprehensively. The scraper crawls **breadth-first from a root** (a space, or a top page) and follows the page tree down, collecting every component it can read:

| Confluence component | Captured |
|----------------------|----------|
| Pages (full tree from root, recursively) | body (storage/HTML), version, ancestors, labels |
| Child pages / nested trees | followed recursively until exhausted |
| Blog posts | body + metadata |
| Attachments | downloaded locally by the scraper → **text extracted** (PDF/DOCX/HTML) → only the text + metadata enter memory; opaque binaries (images w/o text) recorded as references, never embedded |
| Comments (inline + footer) | captured and linked to their page |
| Labels / spaces / page properties | captured as entities for the index |

**Binaries do not enter the memory ZIP.** The scraper downloads an attachment on the user's machine, extracts its text there, and the export carries the *text*, not the bytes. This is what keeps the single-ZIP assumption viable even with hundreds of attachments — otherwise a few hundred PDFs would blow the memory budget on the first ingest. (Text extraction happens scraper-side because that's where the binaries already are and where deps are unconstrained.)

The crawl is **resilient and resumable**: it records a cursor + per-item `content_hash`, so a re-run is incremental and a network drop doesn't force starting over. It is **greedy but bounded** — it collects as much as the token can see, with configurable depth/size caps and a clear report of anything it had to skip (no silent truncation). Jira is scraped in the same spirit: all issues in scope, plus comments, transitions, and links.

### 8.2 The export contract

`export_schema.json` is the lockstep between scraper and digest — it is *versioned*, and digest refuses an export whose schema version it doesn't understand (and tells the user to fetch the matching scraper):

```jsonc
{
  "schema_version": "1.0",
  "scraped_at": "2026-05-29T09:00Z",
  "scope": {"jira_jql": "project = PROJ", "confluence_root": "SPACE", "depth": "unbounded"},
  "counts": {"issues": 412, "pages": 1340, "attachments": 880, "comments": 2200},
  "skipped": [ /* anything the crawl could not fetch, with reason — never silent */ ],
  "items": [ /* per-issue JSON, per-page body+meta+comments, attachments manifest */ ]
}
```

The token is read from the user's environment by `scrape.py` and is **never** transmitted to or seen by the agent — which is the whole reason the scraper is external (the agent has no network). Read-only enforcement and the never-modify mandate are covered in §1.1.

---

## 9. The evolving curated layer (how the agent "grows")

This is the tier that makes the agent more than a search box over the latest dump. The agent authors curated KUs — via the Librarian, with provenance — and they accumulate across ingests:

| Category | What it holds | Example |
|----------|---------------|---------|
| `glossary/` | Project/domain terms the agent has pinned down | "MPK = miejsce poboru, maps to MeterPoint in SF" |
| `mappings/` | Confirmed cross-source bridges | "Mule `meterPointSync` ⇄ SF `MeterPoint__c` ⇄ Domain PL-0123" |
| `decisions/` | ADR-style notes on how/why something is built | "Bulk import retries handled in Mule, not Apex — see PROJ-201" |
| `lessons/` | Answered questions / playbooks worth keeping | "How to trace a failed meter sync end-to-end" |
| `standards/` | Best-practice KBs (Mule, SF) | ported + curated over time |

**Provenance + staleness is what keeps growth honest:**

- Every curated KU carries `derived-from` links to the raw KUs it summarizes.
- When a re-ingest **supersedes** an underlying raw KU (its `content_hash` changed), the Librarian flags every dependent curated KU as `needs-review` rather than silently leaving it to look authoritative. (Invariant I6 + the digest conflict path in §6.)
- The agent surfaces `needs-review` curated KUs proactively, and re-confirming or updating them is itself a governed Librarian commit.

This is the concrete answer to *"smoothly grow and keep the most important things"*: growth is an append to a provenance-linked, staleness-aware tier, and reorganization of that tier goes through `plan_reorganization` with a preview the user approves.

---

## 10. What we drop from the studied framework, and why

| Dropped | Reason (analysis finding) |
|---------|---------------------------|
| Dual-platform (Host-A vs Copilot), `StorageAdapter` monkey-patch | Single host now. Removes a whole class of "works in exec, NameError on import" coupling. |
| `AgentFactory/` (AgentGen, IdentityGen, AgentPackager, TemplateGen) | We build one agent, not a factory of agents. ~2000 LOC of mostly-orphan/dead code. |
| Shared-globals `exec()` loader | Replaced by real importable modules + pytest (finding E: the single biggest rewrite blocker). |
| Static `_DOMAIN_KEYWORDS` / `_DOMAIN_ENTITIES` router | Replaced by generated `routing.json` from real content + the entity bridge (finding #1). |
| Markdown L0/L1/L2 as a database | Replaced by sqlite indexes + a generated routing table; the L0→L1→L2 *spirit* (route, don't dump) is kept. |
| Three manifest writers / three schemas / stored `stats` | One writer (`manifest.py`), one schema, computed stats (findings #2, #5). |
| Module-global session state | On-disk `dev/session_state.json` (finding #3). |
| 1940-LOC `Verify` god-class | Split: `changelog.py` (gate), a read-only checker, no release-pipeline-vs-checker tangle (finding #7). |

**Kept (with adaptation):** the confidence model (✅/🟡/🟠/🔴), the conflict-priority order, the per-source indexing strategy, the Domain KB, the "route, don't dump into context" discipline, the changelog-before-release ritual (now actually enforced).

---

## 11. Build order

| Phase | Scope | Why first / risk |
|------:|-------|------------------|
| 0 | **Scope spec + eval set** (20 queries, sample Jira/Conf/Mule/SF) | Nothing is testable without it. |
| 1 | **Librarian core** — ✅ **implemented** in [`../librarian/`](../librarian/): `schema.py`, `manifest.py`, `changelog.py`, `session.py`, `store.py`, `librarian.py`; invariants I1–I6, I8, I9, I11, I12, I13 + atomic ZIP swap; 51 pytest tests in [`../tests/`](../tests/) (`.venv/bin/pytest`) span the full suite. I7/I10 land with the index/digest builders. | The spine. Everything else commits through it. Built and tested in isolation — real importable modules, no shared-globals exec. |
| 2 | **Bootstrap + session loop** — ✅ **implemented**: [`librarian/bootstrap.py`](../librarian/bootstrap.py) (`boot()` + `Session` with auto-checkpoint), [`MASTER_PROMPT.md`](../MASTER_PROMPT.md) (the operating contract — pasted into the builder, *outside* the ZIP), [`scripts/build_memory.py`](../scripts/build_memory.py) deployable-ZIP builder. Verified by a clean-room boot: a 20 KB `memory.zip` imports the engine from *inside* itself and runs a commit cycle on an interpreter with no `librarian` installed. | Proves the ZIP-in/ZIP-out memory cycle end to end. |
| 3 | **Digest: Salesforce** — ✅ **implemented** ([`librarian/digest/graphbuilder.py`](../librarian/digest/graphbuilder.py), backed by the vendored [`vendor/graphbuilder/`](../vendor/graphbuilder/) engine incl. [`omnistudio.py`](../vendor/graphbuilder/omnistudio.py)): parses `force-app` → objects/fields, Apex, triggers, flows, LWC, **flexipages, permission sets, profiles, permission set groups** raw KUs + a typed graph (field_of, lookup, on, calls, references, touches, uses-component, page-for, embeds, grants, contains, maps, uses). Names handled standard/custom/**packaged**; referenced-but-not-retrieved objects become **external stub nodes** so object-centric queries stay complete. Queries: `fields_of`/`triggers_on`/`who_calls`/`calls_of`/`flows_touching`/`components_using`/`grants_on`/`pages_for` + generic `neighbors`/`dependencies`/`dependents`. **OmniStudio** (OmniScripts, Integration Procedures, Data Mappers, FlexCards; standard `*.os/oip/rpt/ouc-meta.xml`, plus Vlocity DataPacks) — ✅ **validated against real Designer-built components**: OmniScript→IP (`integrationProcedureKey`) and →Data Mapper (`bundle`) from nested `<omniProcessElements>/<propertySetConfig>`; IP→Data Mapper; Data Mapper→SObject from structured `<omniDataTransformItem>` (`inputObjectName`). Canonical naming `Type_SubType` (OS/IP) / `Name` (DM), active-version dedup. **Caveat:** managed/file-based industry components (e.g. an E&U solution) aren't exposed to the Metadata API — those need OmniStudio's DataPack/migration export (the Vlocity-DataPack path handles that JSON). | Structured sources first; exact-answer graph queries are the easy wins. |
| 3b | **Digest: Mule** — ✅ **implemented** ([`librarian/digest/mule.py`](../librarian/digest/mule.py)): one KU per Mule config file; flows/sub-flows/connectors as graph nodes; `<flow-ref>` → `calls` edges, connectors → `uses` edges; cross-file flow-refs become file→file links; flow names join the entity bridge. Queries: `flow`/`who_calls`/`calls_from`/`connectors_used`/`flows_using`/`search_flows`. Synthetic-tested; validate against a real Mule app when available. | Same graph approach as Salesforce. |
| 4 | **Scraper + export contract + digest: Jira/Confluence** — recursive crawl from root (§8.1), read-only (§1.1), `normalize_pl.py` + `pl_lemmas.sqlite` | The hard retrieval source; the escape hatch (§7.4) de-risks it. |
| 5 | **Retrieve / entity bridge** — ✅ **implemented** ([`librarian/index.py`](../librarian/index.py) + [`retrieve.py`](../librarian/retrieve.py)): a source-agnostic search index (a serialized SQLite KU `agent:index/search`) holding the **entity bridge** (`entities` table) + **FTS5** over title/entities/body, rebuilt idempotently via `rebuild_indexes()`. Primitives: `find_entity` (cross-source by name), `cross_source` (join grouped by source), `search` (BM25 + snippets), `entity_like`. Validated on the sample org (341 entity links, 199 FTS docs). LLM query-rewrite is the agent's job at runtime; embeddings still deferred. | The bread-and-butter ASK path. |
| 6 | **Generation — MUnit + Apex tests + artifacts** — Mule flow → MUnit, Apex class → test class, against the curated best-practice standards | In v1 scope. Biggest *quality* risk (useful vs. scaffold tests); depends on the graphs (Phase 3) + standards. |
| 7 | **Domain port** — lift JSON → `domain.sqlite`, reuse parsers | Mostly mechanical. |
| 8 | **Curated layer + reorganization** — `plan_reorganization`, staleness flagging, `needs-review` surfacing | The "grow" feature; depends on the Librarian being solid. |
| 9 | **Prompt engineering + integration + eval against Phase 0 set** | Tune routing, confidence, context budget. |

Phase 1 is the highest-leverage and the most different from the studied framework — it is where "a method, not a guideline" gets built. I'd start there immediately after the scope spec exists.

---

## 12. Open questions

**Resolved (2026-05-29):**

- **Memory is one ZIP**, with an easy fallback to multiple ZIPs (e.g. one per source) if the corpus outgrows it. v1 assumes the monolith; the manifest/bootstrap is designed so sharding is a later config change, not a redesign. *(was Q1 + Q6)*
- **The sandbox persists the working dir across tool calls within a session** — confirmed. The session loop (§5) relies on it. *(was Q3)*
- **MUnit / Apex test generation is in v1 scope** — Phase 6 in the build order. *(was Q5)*
- **Read-only / no write-back** is settled and elevated to the non-negotiable safety principle (§1.1) — it is no longer an open question.
- **Serial use assumed** — one active session mutates memory at a time. A manifest *generation counter* (§3.4) guards against a stale overwrite (a commit refuses if the on-disk manifest generation differs from the one it loaded), but concurrent multi-session editing of the same memory ZIP is out of v1 scope.

**Still open:**

| # | Question | Needed by |
|---|----------|-----------|
| 1 | Wheelhouse budget — acceptable cold-start cost / ZIP overhead for bundled wheels (lxml, tree-sitter-apex; sqlite is stdlib)? | Phase 3 |
| 2 | Reply language — always Polish, or match the query language? | Phase 9 |
| 3 | Confluence crawl scope — start from a single space root, or the whole instance? And attachment size/type caps for the greedy crawl (§8.1)? | Phase 4 |

---

## 13. Status of this document

This is the **canonical, current architecture**. The detailed build plan, the
outstanding-decisions list, and the analysis of the studied framework that
informed these choices are kept as internal notes outside the tracked repo; where
this document differs from any earlier framing, this document wins. The companion
[`INDEXING.md`](INDEXING.md) covers the per-source indexing strategy in depth.

---

## 14. Extensibility — adding sources and skills later

The whole point of the Librarian-as-spine design is that **growth happens at the edges, not the core.** Two kinds of growth:

### 14.1 Adding a new knowledge source (e.g. Azure DevOps, SharePoint, ServiceNow, another repo type)

It's **two changes, and nothing in the core moves:**

1. Add the source to the `SOURCES` vocabulary in `schema.py` (one line). The id namespace (`newsrc:...`), tiers, manifest, changelog, persistence, and all 13 invariants then apply to it automatically.
2. Write one `digest/<source>.py` that parses the input into KUs (+ an optional derived graph) and commits via `lib.begin(...).ingest_ku(...).commit()` — exactly the shape `digest/mule.py` already follows (`parse → to_kus → ingest`).

That's it. The Librarian, the atomic commit, the idempotent re-ingest, the staleness flagging, the ZIP cycle — all reused unchanged. This is the concrete payoff of *not* tangling source-specific logic into the engine (the mistake we documented in the studied framework, where Salesforce vocabulary leaked into every code path).

What the source author also gets for free: cross-source joins. Entities are source-agnostic, so once the entity bridge (`kb/indexes/entities.sqlite`) exists, a new source's entities join against every existing source with no extra work — "which `<newsrc>` items touch `MeterPointService`?" just works.

Boundaries to respect so it stays clean:
- Each source owns its **graph node namespace** (`object/…`, `flow/…`, `<newsrc>/…`). Cross-source links go through the entity bridge, not by reaching into another source's graph.
- KU `links` use only the schema's `LINK_KINDS` (coarse `references`, `derived-from`, …); precise typed edges live in that source's graph artifact, not in the manifest.
- A genuinely new artifact shape may need one new `kind` value — again a one-line vocab add.

### 14.2 Adding a new skill (a capability: MUnit generation, doc generation, a new analysis or query type)

Skills layer **on top of** the Librarian + retrieve; they are not part of the memory spine.

- A skill is a normal importable module (e.g. under `librarian/skills/`) — testable, no shared-globals. It **reads** via graph/retrieve queries and, if it produces knowledge worth keeping, **writes** through the Librarian like any curated mutation (governed, validated, changelog'd).
- Skills fit the existing KU model: they can ship as `kind: tool` / `kind: instruction` built-in KUs, so they're versioned and changelog'd like everything else. The scraper is already exactly this pattern.
- Pure-output skills (generate MUnit/Apex tests, generate a doc) are functions of `raw + curated + standards` — they need no new memory machinery; they read the graph, apply a standard, emit an artifact.
- Knowledge-growing skills (e.g. "summarize this Confluence space into a curated brief") go through `lib.begin(...).add_ku(...).commit()` so the same governance and provenance apply.

If skills proliferate, add a small **generated** skill registry + routing ("which skill for this request") — the same principle as the routing table: derived from what's installed, never a hand-maintained hardcoded dict.

### 14.3 Where the design will strain (and the planned answer)

| Pressure | Planned answer |
|----------|----------------|
| Many sources × large corpora exceed one ZIP | Multi-ZIP shard fallback (designed in §12; manifest/bootstrap implement it when needed) |
| A single source's graph gets very large as one JSON KU | Move that source's graph into a sqlite artifact in the `indexes` tier (same KU contract, different storage) |
| Cross-source value depends entirely on the entity bridge | Build it well and early — it's the linchpin (implemented in `librarian/index.py`) |
| Two skills writing overlapping curated KUs | Already handled by the transaction model (atomic, validated), but adopt a naming convention for curated namespaces to avoid collisions |

The headline: **adding a source is a vocab line + a digest module; adding a skill is a module that talks to the Librarian.** The spine — manifest, transactions, invariants, persistence — is fixed and reused, which is exactly what keeps the agent able to "smoothly grow" without drifting into a mess.
