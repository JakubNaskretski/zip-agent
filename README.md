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
| [`librarian/`](librarian/) | The engine — transactional KB mutation (the Librarian), manifest, schema, changelog, bootstrap, retrieve/index, and [`digest/`](librarian/digest/) source adapters (Salesforce, Mule, Jira, Confluence, office documents). Real importable modules, `pytest`-tested. Zero runtime dependencies (stdlib only). |
| [`vendor/graphbuilder/`](vendor/graphbuilder/) | Vendored Salesforce/OmniStudio metadata-graph parsing engine (plus the Mule/Jira/Confluence extractors and the read-only Jira/Confluence collectors), used by the [`digest/`](librarian/digest/) adapters. |
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

The Librarian engine, the Salesforce, Mule, Jira, Confluence and office-document
(`.docx`/`.xlsx`) digests, the read-only Jira/Confluence collectors (vendored
with the engine), and the cross-source retrieve / entity-bridge are
**implemented and tested**. Still to
come: MUnit/Apex test generation and the built-in Domain KB port.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest          # the suite should pass
```

## Using it — end to end

Everything below assumes a clone of this repo and a stock `python3` (3.9+ is
enough for building the ZIP and running the collectors — no install, no venv;
the dev test suite targets 3.11+). The agent side only needs a code-interpreter
sandbox that can run Python and keep one file (`memory.zip`) between sessions.

### 1. Build the deployable ZIP — pick A or B

**A. Basic — regex Apex parser:**

```bash
python3 scripts/build_memory.py memory.zip
```

**B. Full (~3 MB) — AST Apex parser, recommended. BOTH commands, in order**
(wheels must match the **sandbox's** platform/Python — the example is
linux x86_64 / Python 3.12 — not your machine's):

```bash
rm -rf wheelhouse/        # pip download APPENDS — always start clean
python3 -m pip download --only-binary :all: --platform manylinux2014_x86_64 \
    --python-version 312 -d wheelhouse/ \
    "tree-sitter>=0.25.2,<1" "tree-sitter-language-pack==0.13.0" "pypdf>=4,<7"
python3 scripts/build_memory.py --wheelhouse wheelhouse/ memory.zip
```

The result is **~4 MB**: the builder automatically slims the language-pack
wheel to the one grammar the agent uses (apex; 20 MB -> 0.3 MB — pass
`--no-slim` to keep all grammars), and `pypdf` rides along for the PDF digest.
A pack 1.x wheel in the dir is refused outright (it downloads grammars at
runtime — unusable offline).

Sanity check B before uploading: `unzip -l memory.zip | grep wheelhouse` must
list five wheels including `tree_sitter_language_pack-0.13.0`. The 0.13 pin is
deliberate and REQUIRED for offline sandboxes: it is the last release that
bundles all grammars in the wheel — pack 1.x downloads grammars from GitHub on
first use, which an offline sandbox cannot do (its node API difference is
handled by the engine's compatibility shim either way). The build
output also states which variant you produced. A wrong-platform wheelhouse
degrades harmlessly at boot: the agent falls back to the regex backend,
exactly as variant A.

### 2. Set up the agent

1. Paste **`MASTER_PROMPT.md`** into the agent builder's *instructions* field —
   it lives outside the ZIP and must be re-pasted whenever it changes.
2. Upload `memory.zip` to the agent's workspace. That file **is** the memory:
   back it up, version it, never hand-edit its contents.

On first contact the agent runs its BOOT protocol (unpack → verify manifest →
auto-install the wheelhouse if present) and reports what its memory contains.

### 3. Feed it Salesforce

Retrieve your org's metadata locally (any `force-app/` tree from
`sf project retrieve start`), zip it, upload it, and ask the agent to digest
it. Under the hood that is:

```python
from librarian.digest import graphbuilder as sf
from librarian import rebuild_indexes
dg = sf.digest("force-app", progress=print)                  # 1: parse + preview
rep, dg = sf.ingest_salesforce(lib, "force-app", author,     # 2: ingest + commit
                               "ingest org metadata", dg=dg)
rebuild_indexes(lib, author, "rebuild after SF digest")      # 3: own execution
```

The agent runs each step as its own short execution (the MASTER_PROMPT "Long
operations" five-call protocol — sandboxes kill long calls and truncate long
stdout), previews the digest (objects, classes, flows, edges, errors), and
asks before committing. Every graph node carries `source_path` back to the file
that defined it, and every source file is readable in full via the raw KU —
the graph is for navigating, the source for detail.

### 4. Feed it Jira and Confluence (on-prem, PAT-only)

Collection runs on **your** machine — the agent never holds your token and the
collectors are strictly read-only (GET-only against the Data Center REST APIs).
The agent hands you the `graphbuilder/` package from its working dir (or use
this repo's `vendor/graphbuilder/`); with the PAT in an env var — never a flag:

```bash
export JIRA_TOKEN=...          # read-only personal access token, env var ONLY
python3 -c "from graphbuilder.jira.collect import collect; \
           print(collect('https://jira.example.internal', ['PROJ'], 'jira-dump'))"
export CONFLUENCE_TOKEN=...
python3 -c "from graphbuilder.confluence.collect import collect; \
           print(collect('https://wiki.example.internal', ['SPACE'], 'confluence-dump'))"
```

Zip the resulting `jira-dump/` / `confluence-dump/` directories, upload them,
and ask the agent to digest — same preview → confirm → ingest flow
(`ingest_jira` / `ingest_confluence`). Re-running a collection is incremental
(unchanged items are skipped) and a partial collection is flagged with an
`.incomplete` sentinel, never silently pruned.

### 5. Feed it documents (Word / Excel / PowerPoint)

Zip a folder of `.docx` / `.xlsx` / `.xlsm` / `.pptx` / `.pptm` documents
(specs, mapping workbooks, slide decks), upload it, and ask the agent to digest
— same preview → confirm → ingest → rebuild-indexes flow:

```python
from librarian.digest import office
od = office.parse_office(docs_dir)        # preview via od.summary(): documents/doc_types/nodes/errors
rep, od = office.ingest_office(lib, docs_dir, author, "ingest project documents", dg=od)
rebuild_indexes(lib, author, "rebuild after docs digest")   # separate execution
```

Per document the agent keeps three things: the **original file** as a raw KU
(re-openable, re-parseable on demand), a **plain-text sidecar** (`.txt` next to
it) holding the extracted text — section titles and body text for Word,
sheet/table/column names for Excel, slide titles + body text + speaker notes +
chart series/category labels for PowerPoint — that is what full-text search
hits — and a contained **structure graph** (Word heading tree; Excel sheets and
declared tables; PowerPoint slides, optional declared sections, and charts;
heuristic guesses are marked as such, structure is never fabricated).
Confidentiality by policy: cell values, formula bodies, numeric chart data and
author names never enter the graph or the sidecar, and document prose is never
entity-bridged. Legacy binary `.doc` / `.xls` / `.xlsb` / `.ppt` are not parsed
(convert them first); `.xlsm` / `.pptm` are parsed with macro content ignored.

### 6. Ask questions

The agent answers from the graphs + full-text search: "which flows touch
MeterPoint__c?", "what does the Acme record page show?", "which Jira tickets
mention this class?", "what does the design doc say about retries?" — and can
always open the underlying source for detail. Each source (Salesforce, Mule,
Jira, Confluence, documents) stays its own contained graph; nothing is
cross-linked automatically.

### 7. Let it grow

Any knowledge the agent adds goes through the Librarian's transaction
(begin → stage → preview → commit) and ends in a **new** `memory.zip` it hands
back to you. Download it and use it as the next session's upload — that file
is the agent's entire state.

### 8. Upgrading the agent (new code, same knowledge)

The ZIP is code **and** state in one file, so shipping a new engine build must
not cost the agent its ingested knowledge. Build the new code ZIP as in step 1,
then merge the deployed agent's state into it:

```bash
python3 scripts/upgrade_memory.py OLD_memory.zip NEW_code.zip -o upgraded.zip
```

State (`kb/**`, `manifest.json`, `dev/` changelog + session state) comes from
OLD; code and assets (`librarian/`, `graphbuilder/`, `reference/` incl. the
wheelhouse) come from NEW. The derived indexes are deliberately **dropped** —
they are always rebuildable and the new code may carry a newer index schema —
so after first boot of `upgraded.zip` the agent must run
`rebuild_indexes(lib, author, rationale)` (the script reminds you). It refuses
to downgrade the knowledge schema and never touches its inputs.

**Manual fallback** — worth knowing even if you never need it: `kb/` +
`manifest.json` + `dev/changelog.json` ARE the whole state; the indexes are
always rebuildable from them. Worst case, copy those three out of the old ZIP
into a freshly built code ZIP and have the agent run `rebuild_indexes`.

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
