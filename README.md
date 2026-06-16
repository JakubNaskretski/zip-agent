# zip-agent

A ZIP-distributed AI agent for a single software-delivery project. The agent runs
inside a code-interpreter sandbox; its **entire persistent memory is one ZIP**
(`memory.zip`), retained across sessions. It is **lightweight by design**: on boot
it loads only a small routing map (not the whole ZIP), answers by reading the
slices a question needs, and saves changes as **single-file writes** — the ZIP is
re-packed only when you explicitly export it. The knowledge is stored as a
**graph per source**, and that graph *is* the navigation index.

This repo is the agent's **engine and build** — not a research write-up.

---

## Quick start

```bash
# 1. BUILD a deployable agent (engine + assembled prompt) into dist/<profile>/
python3 scripts/build_memory.py --profile rfp
#   → dist/rfp/memory.zip   (upload this as the agent's memory)
#   → dist/rfp/MASTER_PROMPT.md   (paste this into the builder's instructions field)

# 1b. BUILD with the AST Apex parser (recommended) and/or the deck-render stack:
python3 scripts/build_memory.py --profile rfp --ast            # better Apex parsing
python3 scripts/build_memory.py --profile rfp --ast --pptx     # + render PowerPoint decks

# 2. UPGRADE / MIGRATE — carry an existing agent's already-ingested KB onto the
#    current engine (works for both older and current zips; no re-parsing):
python3 scripts/build_memory.py --profile rfp --migrate /path/to/old_memory.zip
#   → dist/rfp/memory.zip with your KB carried in (backs up any previous one)

# 3. DEPLOY: paste dist/<profile>/MASTER_PROMPT.md into the agent builder's
#    instructions field, and upload dist/<profile>/memory.zip as its memory.
```

Profiles are `rfp` (a Salesforce RFP-pursuit co-pilot) and `project` (the general
agent). `--list-profiles` lists them; see [`profiles/README.md`](profiles/README.md).
A stock `python3` (3.9+) is enough to build and to run the collectors — no install.

**Dev / tests:**

```bash
uv run pytest            # or: python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest
```

---

## What ships in the agent (`memory.zip`)

The graph is the index: each source becomes a shard, projected into a small L0 map
plus per-source L1 aids. Knowledge is read on demand; nothing is held in memory.

| In the ZIP | What it is |
|------------|------------|
| `agent_manifest.json` | What's inside + what to load on boot. |
| `index/L0.md` | The **knowledge map** — sources, counts, routing. The one thing boot pulls into context. |
| `index/L1/<source>.md` | Per-source routing aid (node types, naming, how to resolve a name in code). Loaded on demand. |
| `graph/<source>.json` | One **structure-graph shard** per source. Loaded into a variable and walked; never dumped into context. |
| `kb/raw/<source>/…` | Verbatim source files (and `.txt` sidecars for document prose). Read by excerpt, on demand. |
| `kb/work/…` + `graph/work.json` | The agent's **work layer** — notes it authors plus the graph edges it draws (incl. cross-source joins). |
| `dev/…` | Durable plan / worklist state (survives a sandbox reset). |
| `runtime/` | **The lightweight engine** — boot, navigation, ingest, search, the doc/work/plan/maintain helpers. The only code imported on boot. |
| `graphbuilder/` | The metadata-graph **parsing engine** — heavy; extracted and imported **only at ingest**, never for answering. |
| `librarian/` | The **digest parsers + schema** (the subset the runtime uses at ingest). |
| `pptx/` | On-demand deck-render skill bundle (the `rfp` profile). |
| `reference/wheelhouse/` | Optional offline wheels (the AST Apex backend / PDF / deck render), installed best-effort at boot. |

`<source>` ∈ `salesforce` · `mule` · `jira` · `confluence` · `docs`.
`MASTER_PROMPT.md` is **not** in the ZIP — it lives beside it and is pasted into
the builder's instructions field.

## What the agent does

| Operation | What happens |
|-----------|--------------|
| **Boot** | Connects to the retained ZIP, unpacks only the small `runtime/`, and loads only `index/L0.md` into context. No whole-ZIP extract, no held engine. Re-running boot cheaply reconnects after a kernel reset. |
| **ASK** | Routes a question through L0 → the right source → loads that graph shard + the slices it needs; walks relationships in code; full-text search for prose. Answers with cited sources + a confidence tier. |
| **DIGEST** | Ingests an uploaded source ZIP: parses it, writes the raw files, merges the source's graph shard, and regenerates the indexes — every change a single-file write (no repack). Re-ingesting unchanged content is a no-op. |
| **GROW** | Builds in the **work layer** (`kb/work/` + `graph/work.json`) — notes it authors and edges it draws between nodes (within and across sources), recording the sources they rest on so a later change can flag them stale. |
| **Export** | On request, packs a **new, versioned** `memory.zip` for you to download — the only place the whole archive is written. |

---

## Building options

`--profile <name>` is the only required flag. The Apex-backend choice is orthogonal:

- **Basic** (regex Apex parser): `python3 scripts/build_memory.py --profile rfp`
- **AST Apex parser** (recommended): add `--ast` (auto-downloads the right wheels for
  the sandbox — default `linux-x64-py312`; pass `--ast linux-x64-py311` etc. for others).
  Needs network **at build time**; the deployed sandbox stays offline.
- **Deck rendering** (`rfp`): add `--pptx` to bundle `python-pptx` so the agent can
  render `.pptx` decks in the sandbox (combine with `--ast`).
- **Custom wheels**: `--wheelhouse DIR` with your own `*.whl` (the `tree-sitter-language-pack==0.13.0`
  pin is required — later versions fetch grammars from the network, which an offline
  sandbox can't do, and the build refuses them).

Sanity check before uploading an AST build:
`unzip -l dist/rfp/memory.zip | grep wheelhouse` should list `tree_sitter_language_pack-0.13.0`.

## Upgrading / migrating

Upgrading the engine and migrating an older agent are the **same operation**: build a
fresh engine and carry an existing zip's knowledge onto it. No re-parsing — the graphs
are already built, so it's a fast relocate-and-reindex that backs up the previous zip:

```bash
python3 scripts/build_memory.py --profile rfp --migrate /path/to/old_memory.zip
# (--upgrade is an alias for --migrate)
```

It carries the raw files, the graph shards, the curated notes, and any bundled
wheelhouse forward, regenerates the indexes, and regenerates `MASTER_PROMPT.md`
(re-paste it if it changed).

## Feeding it data

You hand the agent ZIPs of source material in chat and ask it to digest; it previews,
confirms, then ingests. Under the hood each digest is one call:

```python
from runtime.ingest import digest_to_tree
digest_to_tree(ws, "salesforce", "/mnt/data/force-app", progress=print)   # or mule / jira / confluence / docs
```

- **Salesforce / Mule** — hand in a `force-app/` tree or a Mule app as a ZIP.
- **Jira / Confluence** — collected on **your** machine by the read-only collectors
  inside the engine (Data Center, Bearer PAT in an env var, never a flag). The agent
  hands you the `graphbuilder/` package (`ws.extract_tree("graphbuilder/")`); you run:
  ```bash
  export JIRA_TOKEN=...        # read-only PAT, env var ONLY
  python3 -c "from graphbuilder.jira.collect import collect; \
             print(collect('https://jira.example.internal', ['PROJ'], 'jira-dump'))"
  ```
  then zip the dump and upload it. The collectors are strictly GET-only; the source
  systems are never modified.
- **Documents** — hand in a folder of `.docx` / `.xlsx` / `.pptx`. The agent keeps a
  media-stripped working copy, a plain-text sidecar (the search surface), and a
  structure graph; cell values and document prose never enter the graph.

See [`docs/retrieving-salesforce-samples.md`](docs/retrieving-salesforce-samples.md)
for getting a Salesforce export to feed it.

---

## Repo layout (development)

| Path | What it is |
|------|------------|
| [`runtime/`](runtime/) | The lightweight engine that ships and runs in the agent — boot, navigation over graph shards, ingest, search, and the doc/notes/plan helpers. Start here for how the agent works. |
| [`vendor/graphbuilder/`](vendor/graphbuilder/) | The vendored metadata-graph parsing engine (Salesforce/Mule extractors + the read-only Jira/Confluence collectors), used by the digest adapters. |
| [`librarian/digest/`](librarian/digest/) | The source parsers the runtime calls at ingest (Salesforce, Mule, Jira, Confluence, office). The rest of `librarian/` is the retired transactional engine, kept for reference but no longer shipped. |
| [`profiles/`](profiles/) | The thin agent factory: the shared base prompt + per-use-case overlays (`project`, `rfp`). |
| [`scripts/build_memory.py`](scripts/build_memory.py) | Builds / migrates the deployable `memory.zip` + assembled prompt. |
| [`tests/`](tests/) | Pytest suite (`test_runtime_*`, digests, factory, migrate). |
| [`docs/`](docs/) | Background. `ARCHITECTURE.md` documents the original transactional design; the current engine is the lightweight `runtime/` summarized above. |

## Constraints (the target environment)

| Constraint | Value |
|------------|-------|
| Runtime | A single code-interpreter host (Python sandbox); the working dir persists within a session; the host model is not hardcoded. |
| Persistence | One `memory.zip`, retained by the host across sessions — the agent's only durable store. |
| Outbound network | None — fully isolated. Only the read-only Jira/Confluence collectors (run on your machine) have network. |
| Package install | From a local wheelhouse bundled in the ZIP. |
| Tenancy | Single project — no multi-tenant logic. |

## License / re-use

This build is independent work; no proprietary code from any studied framework is
redistributed here. The parsing engine under [`vendor/`](vendor/) is vendored per its
own terms (see [`vendor/README.md`](vendor/README.md)).
