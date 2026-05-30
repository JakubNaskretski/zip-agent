# Next Steps — Decisions and Deliverables Before Code

The plan in `00_PLAN.md` is sized assuming five inputs exist. Until they do, building anything is premature optimisation.

## A. Five things that need to exist before Phase 1

### A1. Scope spec — top-5 user questions

Fill in the table below with **real** queries your users actually ask. The retrieval evaluation set is built from these.

| # | User question (verbatim, in user's language) | Source(s)             | What "good answer" looks like                                    |
|---|----------------------------------------------|-----------------------|------------------------------------------------------------------|
| 1 |                                              | Mule / SF / J / C / Domain |                                                                  |
| 2 |                                              |                       |                                                                  |
| 3 |                                              |                       |                                                                  |
| 4 |                                              |                       |                                                                  |
| 5 |                                              |                       |                                                                  |

If you can do 20 instead of 5, even better — they become the eval set.

### A2. One real (or anonymised) Jira ticket + one real Confluence page

Used to calibrate Polish lemmatization quality and entity extraction. The messier the better — pick docs that are typical, not the cleanest ones. Drop into `samples/` (gitignored).

### A3. Mule sample directory

A subdirectory of the real Mule repo, ~5–15 files, containing at least one flow that calls another flow (so the graph builder has something non-trivial to chew on). Drop into `samples/` (gitignored).

### A4. Salesforce sample directory

Same as A3 — `force-app/` subset with at least one Apex class that references an SObject field, one trigger, one flow. `samples/` (gitignored).

### A5. Two architectural decisions

#### Decision D1: Embeddings — yes or no for v1?

| Choice          | Pros                                                | Cons                                                                                  |
|-----------------|-----------------------------------------------------|---------------------------------------------------------------------------------------|
| **No** (default) | ZIP stays under 10 MB. No model download. Simpler. | Might miss recall on synonym-heavy Polish prose queries.                              |
| **Yes** (offline-baked) | Better Confluence Q&A recall (5–10% more).        | +150 MB to ZIP. Cold-start 30 s. More moving parts.                                    |

**Recommendation:** start with **No**. Add later as a rerank-only pass if v1 eval shows recall gaps.

#### Decision D2: Builder host

| Choice                  | Pros                                          | Cons                                              |
|-------------------------|-----------------------------------------------|---------------------------------------------------|
| **Laptop script**         | Simplest. User runs it as needed.            | Each user re-runs separately; no shared cache.    |
| **CI job** (GitHub Actions etc.) | Centralized, scheduled refresh.        | Needs secret storage for tokens.                  |
| **Both**                  | Power users run locally; team has nightly CI build. | More code.                                  |

**Recommendation:** start with laptop script. CI follows once the script is solid.

## B. Open architectural questions

These don't block Phase 0–1 but should be decided by Phase 3.

| # | Question                                                                                          | When to decide  |
|---|---------------------------------------------------------------------------------------------------|-----------------|
| 1 | How do MUnit tests get exported? Files into the user's repo? Just printed as code blocks?         | Phase 2 (Mule)  |
| 2 | Do we want a "diff vs. previous build" report when the ZIP is regenerated? (e.g. "12 new pages, 3 modified, 1 removed since last build") | Phase 7 |
| 3 | Is there value in a `confluence_page_diff(old_version, new_version)` runtime tool?                | Phase 4         |
| 4 | Should the agent be able to write *back* to Jira (post comments, create tickets)? Currently read-only. | Phase 4    |
| 5 | Multi-language UX — should the agent always reply in Polish? Or match user's query language?      | Phase 8         |
| 6 | What's the auth story for the CI builder if we go that route?                                     | Phase 7         |

## C. Risks to flag now

| Risk                                                                                       | Mitigation                                                                                       |
|--------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| **Polish lemmatization quality** — stanza/spacy can mis-lemmatize technical jargon         | Build the eval set (A1) first. If quality is poor, swap in `morfeusz2` (better but C-deps).      |
| **Confluence HTML cleanup** — pages have tables, macros, attachments                       | Spend extra time on the normalizer. Bad input = bad index.                                       |
| **MUnit usefulness** — generating *correct* tests vs *useful* tests is the gap             | Bound v1 to "scaffold" tests; mark "useful" as a Phase-2 target.                                 |
| **SF Apex parsing** — tree-sitter-apex is community-maintained, may have gaps              | Fall back to regex for the cases tree-sitter misses; document the boundary.                      |
| **Domain drift** — the public docs update occasionally                                       | The existing Domain work has a parser we can reuse. Refresh manually when public docs revise.     |
| **Tool call latency in the sandbox** — every Python tool call has overhead                  | Batch operations where possible; design `retrieve(...)` to return enough for a follow-up answer in one call. |
| **Wheelhouse maintenance** — pip-install-at-runtime needs a curated wheel bundle           | Standardize early; check it into the ZIP. ~50 MB.                                                |

## D. What to deliver in this repo as Phase 0 outputs

When A1–A5 are done, the repo should grow:

```
zip-agent/
├── README.md                # already here
├── analysis/                # already here
├── plan/                    # already here
├── scope/                   # NEW
│   ├── queries.md           # the 20-query eval set (filled-in A1)
│   ├── decisions.md         # D1 + D2 decisions + rationale
│   └── risks.md             # confirmed risk register
└── samples/                 # gitignored — local only
    ├── jira/
    ├── confluence/
    ├── mule/
    └── sf/
```

Once `scope/` is in place, Phase 1 (runtime framework) can start. Until then, building tools is premature.

## E. How to pick this up in a new chat / web session

If switching sessions:

1. Paste the repo URL or upload it
2. Tell the new session: *"Read README.md, then read plan/00_PLAN.md. We're at Phase 0 — figure out what's missing in `scope/` and ask me for it."*
3. Work continues from there

The README is the entry point. Every other doc references back to it.
