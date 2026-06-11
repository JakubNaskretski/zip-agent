# zip-agent

A ZIP-distributed AI agent definition for a single in-progress enterprise
software-delivery project. The agent runs inside an isolated code-interpreter
sandbox; its **entire persistent memory is one ZIP**, which it digests data into
and reorganizes itself through a governed, transactional method — the
**Librarian**. The host model is not hardcoded.

This repo is the agent's **engine and build** — not a research write-up.

## What's in this repo

| Path | What it is |
|------|------------|
| [`librarian/`](librarian/) | The engine — transactional KB mutation (the Librarian), manifest, schema, changelog, bootstrap, retrieve/index, and [`digest/`](librarian/digest/) source adapters (Salesforce, Mule). Real importable modules, `pytest`-tested. Zero runtime dependencies (stdlib only). |
| [`vendor/graphbuilder/`](vendor/graphbuilder/) | Vendored Salesforce/OmniStudio metadata-graph parsing engine, used by [`digest/graphbuilder.py`](librarian/digest/graphbuilder.py). |
| [`MASTER_PROMPT.md`](MASTER_PROMPT.md) | The agent's persona + operating protocols — pasted into the agent builder's instructions field, **outside** the ZIP. |
| [`scripts/build_memory.py`](scripts/build_memory.py) | Builds the deployable `memory.zip` — the engine packaged inside its own memory. |
| [`tests/`](tests/) | Pytest suite for the engine and digests. |
| [`docs/`](docs/) | How the agent works and how to use it — [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) (the spec the code implements), [`INDEXING.md`](docs/INDEXING.md) (per-source retrieval strategy), [`retrieving-salesforce-samples.md`](docs/retrieving-salesforce-samples.md) (feeding it test data). |

**Start with [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** for the design — the
single-host living-memory model, the Librarian, and the I1–I13 invariants.

The internal build plan, todo/status notes, and the analysis of the framework that
motivated this design are kept as **local-only material under `private/`**
(gitignored), along with any example/sample data — they are not part of the
shippable agent.

## Status

The Librarian engine, the Salesforce and Mule digests, and the cross-source
retrieve / entity-bridge are **implemented and tested**. Still to come: the
read-only Jira/Confluence scraper + digest, MUnit/Apex test generation, and the
built-in Domain KB port.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest          # the suite should pass
```

## What the agent is for

Single-project, single-tenant. Knowledge spans:

- **Salesforce repository** (parsed in-sandbox by the vendored graph-builder
  metadata-graph engine) — answer questions about Apex/objects/triggers/flows,
  suggest best practices
- **Mulesoft repository** (parsed in-sandbox) — answer "what does this flow do?",
  generate MUnit tests, identify cross-flow dependencies
- **Jira** (scraped on-prem via the agent's read-only scraper + user token) —
  answer questions about tickets, find tickets touching specific services
- **Confluence** (same pattern as Jira; recursive crawl from a root) — answer
  prose questions about project documentation
- **Domain** (built-in) — domain knowledge for the sector regulatory context

The agent runs inside an isolated code-interpreter sandbox (no outbound network,
pip-install from a local wheelhouse). Its memory is **one ZIP, retained across
sessions**, which the agent digests data into and reorganizes itself through the
Librarian. The only external component is a strictly **read-only scraper** the
agent hands the user for Jira/Confluence — the source systems are never modified.
Mule/Salesforce repos are handed in as ZIPs and parsed in-sandbox.

## Constraints (verified from the host environment)

| Constraint            | Value                                                                  |
|-----------------------|------------------------------------------------------------------------|
| Runtime               | Single code-interpreter host (Python 3.11+ sandbox); working dir persists within a session; host model not hardcoded |
| Persistence           | One memory ZIP, retained by the host across sessions — the agent's only durable store |
| Outbound network      | None — fully isolated (only the local scraper has network)             |
| Package installation  | pip install from a local wheelhouse bundled in the ZIP                  |
| Tenancy               | Single project — no multi-tenant logic                                  |
| Document language     | Mostly Polish for Jira/Confluence; English for code; bilingual Domain   |
| Refresh model         | Agent digests in-sandbox; only Jira/Confluence scraping is external (read-only), user-triggered |

## License / re-use

This build is fresh, independent work. No proprietary code from any studied
framework is redistributed here. The Salesforce parsing engine under
[`vendor/`](vendor/) is vendored per its own terms (see
[`vendor/README.md`](vendor/README.md)).
