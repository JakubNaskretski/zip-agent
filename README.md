# zip-agent

Research and design work for a ZIP-distributed AI agent definition, intended to be uploaded into a code-interpreter sandbox host model and used to support a single in-progress enterprise software-delivery project.

This repo contains two things:

| Directory       | What's in it                                                                                                         |
|-----------------|----------------------------------------------------------------------------------------------------------------------|
| [`analysis/`](analysis/) | Verification of an existing ZIP-based agent framework I studied — architecture, runtime mechanics, KB catalog, workflows, and a mechanic verification report covering ~12K LOC of core engine code. |
| [`plan/`](plan/)         | The build plan for the new agent: scope, two-codebase architecture (builder + runtime), per-source indexing strategy, anti-patterns to avoid, effort estimate, next steps. |

The plan is informed by the analysis — every architectural choice in `plan/` points back to a specific issue documented in `analysis/`.

## Project status

**Phase 0 — pre-build.** The precise architecture is settled in [`plan/03_ARCHITECTURE.md`](plan/03_ARCHITECTURE.md) — a single-host, living-memory agent governed by the *Librarian* method (the host model is not hardcoded). Concrete scope inputs (sample data + eval queries) are still pending; see [`plan/02_NEXT_STEPS.md`](plan/02_NEXT_STEPS.md).

## How to read this in order

If you're picking this up fresh (in a new chat session or on the web):

1. **Read [`README.md`](README.md)** — this file
2. **Read [`plan/03_ARCHITECTURE.md`](plan/03_ARCHITECTURE.md)** — ⭐ the current, precise architecture (living-memory agent, the Librarian). **Start here** — it supersedes the older framing in places noted below.
3. **Read [`plan/01_INDEXING.md`](plan/01_INDEXING.md)** — the per-source indexing strategy (still current; now runs in-sandbox)
4. **Read [`plan/00_PLAN.md`](plan/00_PLAN.md)** — the original build plan; still the reference for scope, effort, and anti-patterns, but its two-codebase / dual-platform framing is superseded by 03
5. **Read [`plan/02_NEXT_STEPS.md`](plan/02_NEXT_STEPS.md)** — the Phase 0 scope inputs still outstanding
6. Skim [`analysis/04_VERIFICATION.md`](analysis/04_VERIFICATION.md) for why the architecture rejects certain approaches
7. Dive into [`analysis/components/`](analysis/components/) only if you need the file:line evidence for a specific finding

## What the agent is for

Single-project, single-tenant. Knowledge spans:

- **Mulesoft repository** (parsed locally) — answer "what does this flow do?", generate MUnit tests, identify cross-flow dependencies
- **Salesforce repository** (parsed locally) — answer questions about Apex/objects/triggers/flows, suggest best practices
- **Jira** (scraped on-prem via the agent's read-only scraper + user token) — answer questions about tickets, find tickets touching specific services
- **Confluence** (same pattern as Jira; recursive crawl from a root) — answer prose questions about project documentation
- **Domain** (built-in) — domain knowledge for the sector regulatory context

The agent runs inside an isolated code-interpreter sandbox on a single host (no outbound network, pip-install from a local wheelhouse; the exact host model is not hardcoded). Its memory is **one ZIP, retained across sessions**, which the agent **digests data into and reorganizes itself** through a governed method (the *Librarian*). Its persona + protocols live in a separate **master prompt** (`MASTER_PROMPT.md`) that is pasted into the agent builder's instructions field — outside the ZIP. The only external component is a strictly **read-only scraper** the agent hands the user for Jira/Confluence — the source systems are never modified ([`plan/03_ARCHITECTURE.md`](plan/03_ARCHITECTURE.md) §1.1). Mule/Salesforce repos are handed in as ZIPs and parsed in-sandbox.

## Why the analysis is here

I studied an existing agent framework — found it on the internet, ran a mechanic verification against ~12K LOC — to figure out what works and what doesn't before building something similar. The verification produced ~50 specific findings tiered by severity (🔴 runtime bug · 🟠 runtime smell · 🟡 design fragility blocking a rewrite).

Top three lessons that drive the new design:

1. **Static hardcoded keyword routers are not retrieval.** The analyzed agent's "domain router" was a frozen dict. New agent uses real indexing (FTS5 + entity-anchors + Polish lemmatization at build time).
2. **One manifest writer, one schema, computed stats.** The analyzed agent had three writers, three schemas, four self-counts of itself, all drifted. New agent: single CRUD path, `stats` is a computed view.
3. **No shared-globals `exec()` trick.** The analyzed agent only works because every tool is `exec()`'d into one shared namespace. Can't be tested. New agent uses normal Python modules, normal `import`, real pytest.

## Constraints (verified from the host environment)

| Constraint                    | Value                                                                  |
|-------------------------------|------------------------------------------------------------------------|
| Runtime                       | Single code-interpreter host (Python 3.11+ sandbox); working dir persists within a session; host model not hardcoded |
| Persistence                   | One memory ZIP, retained by the host across sessions — the agent's only durable store |
| Outbound network              | None — fully isolated (only the local scraper has network)            |
| Package installation          | pip install from a local wheelhouse bundled in the ZIP                 |
| Tenancy                       | Single project — no multi-tenant logic                                 |
| Document language             | Mostly Polish for Jira/Confluence; English for code; bilingual Domain   |
| Refresh model                 | Agent digests in-sandbox; only Jira/Confluence scraping is external (read-only), user-triggered |

## License / re-use

This repo is design work and analysis only. No proprietary code from the studied framework is included — only my own notes, file-line citations to fragments I'm not redistributing, and design proposals for a new, independent implementation. The new build is fresh from scratch.

---

**Picking up in a new session?** Copy this prompt into a new chat after opening the repo:

> Read `README.md`, then `plan/03_ARCHITECTURE.md` (the current architecture). We're at Phase 0. Tell me what's blocking Phase 1 and what concrete inputs you need from me first.
