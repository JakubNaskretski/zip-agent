# Agent Definition — Architecture Overview

**Package analysed:** `AgentDefinition_v1.0.26/`
**Source manifest version:** `2.0`, agent `Agent Factory` v1.0.23 (folder labelled v1.0.26)
**Date of analysis:** 2026-05-24

> Companion files in this folder:
> - [`01_RUNTIME.md`](01_RUNTIME.md) — bootstrap, resource routing, indexing, context budget
> - [`02_KB_AREAS.md`](02_KB_AREAS.md) — naming convention + full tool/instr/KB catalog per area
> - [`03_WORKFLOWS.md`](03_WORKFLOWS.md) — generation (G1–G5), dual-platform, dev protocol, changelog/merge

---

## 1. What this package is

The user's assumption is correct: **`AgentDefinition.zip` is a self-contained "agent definition" — a manifest + a tree of tools, instructions, knowledge bases, indexes and templates that an external LLM (the *host*) consumes at session start to become a domain-specialised agent.**

Specifically, this particular ZIP is the **Agent Factory** — the *root* / *bootstrap* agent. Its primary purpose is to generate **other** agents (project-, technology- or domain-specific). It is also an agent itself and uses the exact same structure it produces for its children.

> A lightweight operational knowledge indexer.
> Lean, context-protective, KB-routed (L0 → L1 → L2), built for software-delivery support work (Q&A, doc generation, diagnostics).

## 2. Mental model — how an external model uses this ZIP

```
┌──────────────────────────────────────────────────────────────────┐
│ Host model (Host-A / GitHub Copilot) │
│ │
│ ┌─ Instructions field ──────────────────────────────────┐ │
│ │ MasterPrompt.md (verbatim — the agent's persona │ │
│ │ + protocols: startup, conversation, indexing, │ │
│ │ context protection, confidence) │ │
│ └───────────────────────────────────────────────────────┘ │
│ │
│ ┌─ Code interpreter / terminal ─────────────────────────┐ │
│ │ 1. exec(Core_Tool_StartupLoader.py) │ │
│ │ 2. load_startup_resources("AgentDefinition.zip")│ │
│ │ 3. StartupLoader reads agent_manifest.json and │ │
│ │ routes each resource to: │ │
│ │ 💾 DISK (Tools, Templates) │ │
│ │ 🐍 PYTHON VAR (large KB, conditional Instr) │ │
│ │ 📋 CONTEXT WIN (active Instr, L0/L1 indexes) │ │
│ └───────────────────────────────────────────────────────┘ │
│ │
│ Agent answers user questions by routing: │
│ L0 (domain) → L1 (file/section) → L2 (load on demand) │
└──────────────────────────────────────────────────────────────────┘
```

The host model never reads tool *source code* into context — tools are extracted to disk and `exec`'d (or run via `python3` on Copilot), so their functions become available without consuming tokens. Large knowledge bases stay in Python variables and are queried in code — only the relevant fragment is printed back into context.

## 3. Top-level package layout

```
AgentDefinition_v1.0.26/
│
├── MasterPrompt.md ← system prompt; copy verbatim into host's Instructions field
├── agent_manifest.json ← machine-readable inventory; drives StartupLoader
├── agent_icon.png ← cosmetic
├── Dev_KB_Changelog.json ← top-level mirror of kb/dev/Dev_KB_Changelog.json
│
└── kb/ ← knowledge base, organised by AREA
 ├── core/ ← agent identity + framework tools (the "engine")
 │ ├── Core_Index_L0.md ← nano routing index (~1–2K tokens)
 │ ├── Core_Index_L1.md ← quick index (5–15K tokens)
 │ ├── Core_Instr_*.md ← active/conditional instructions
 │ ├── Core_Tool_StartupLoader.py ← bootstrap entry point
 │ ├── Core_Tool_StorageAdapter.py← ZIP↔folder transparency
 │ ├── AgentFactory/ ← G1–G5 generators (AgentGen, IdentityGen, AgentPackager…)
 │ ├── Agent Extension/ ← Verify, Repair, Report, AgentExtend, KBManifest, KBSchema, ChangelogWriter
 │ ├── Indexing/ ← RebuildIndexes, DomainStub, RuntimeHelpers
 │ └── Menu & Config/ ← Menu builder, configurators (JSX), ConfiguratorRegistry
 │
 ├── common/ ← reusable tools shared across all agents
 │ ├── Common_Tool_Budget.py / Tracker.py / Session.py / Requirements.py
 │ ├── Azure Dev Ops import/ ← ADO CSV → LLM-ready
 │ ├── Confluence import/ ← Confluence → MD KB
 │ ├── EA BPMN import/ ← Enterprise Architect XMI → LLM BPMN
 │ ├── Doc Generator/ ← DOCX/PPTX generators + templates
 │ ├── User Story/ ← User-story generator
 │ └── Implementation Approach/ ← Impl-approach generator
 │
 ├── tech/ ← technology-specific tools
 │ └── salesforce/ ← SF Pull/Push/Diff/Audit/CodeGen/Knowledge/AgentGen/CopilotAgentGen + 11 sub-instructions
 │
 ├── bus/ ← business-domain knowledge
 │ ├── energy/ ← Polish energy-sector dictionary
 │ └── enrg/ ← Domain knowledge base (14 KB files + 2 parsers + 4 instr) for PL energy market
 │
 └── dev/ ← agent's own dev state (changelog, requirements, chronicle, baselines)
 ├── Dev_KB_Changelog.json ← L1 changelog summary
 ├── changelog/v{X}_details.json← L2 per-version diffs
 ├── Dev_KB_RequirementsDB.json + .md
 ├── Dev_KB_ApiBaseline.json
 ├── DEV_KB_Chronicle.md
 └── Dev_KB_VerifyHistory.json
```

## 4. Naming convention — `{Area}_{Type}_{Name}.{ext}`

Every artefact's filename encodes both **where it lives** (area) and **what it is** (type). The Factory enforces this when it generates child agents.

**Area prefix:**

| Prefix | Scope |
|-----------|-----------------------------------------------------------------------------|
| `Core_` | Agent identity, prompt, configurators, core instructions and tools |
| `Common_` | Reusable tools shared across agents (neither tech- nor domain-specific) |
| `Tech_` | Tech-stack artefacts (e.g. `Tech_SF_` = Salesforce, `Tech_MULE_` = Mulesoft)|
| `Bus_` | Business-domain artefacts (e.g. `Bus_ENRG_` = energy, `Bus_BNK_` = banking) |
| `Proj_` | Project-specific (only in generated child agents — not in the Factory itself) |
| `Dev_` | Agent's own dev artefacts — requirements, changelog, chronicle |

**Type tag:**

| Tag | Purpose |
|--------|----------------------------------|
| `Instr`| Operational instruction (.md) |
| `Tool` | Executable code (.py / .jsx) |
| `KB` | Knowledge article (.md / .json) |
| `Index`| Search index (L0 / L1) |
| `Tmpl` | Document template (.docx / .pptx)|
| `Test` | Test |

## 5. The 11 domains (D01 – D11)

The agent's capability surface is split into 11 routed domains. The L0 index maps user questions to a domain via keyword/entity tables; L1 then narrows to file(s); L2 is on-demand load.

| ID | Domain | What lives there |
|-----|------------------------------|-----------------------------------------------------------------------------------------------------------|
| D01 | Agent Generation | Create agents from presets (G1–G5) |
| D02 | Agent Maintenance | Verify, Repair, Extend, Report (V1) |
| D03 | Salesforce | SF metadata: pull, push, diff, audit, code-gen, code-explain (SF1–SF5) |
| D04 | Document Generation | branded DOCX/PPTX from markdown (DOC) |
| D05 | Data Import | ADO, Confluence, EA BPMN → KB |
| D06 | User Stories | Generate/manage user stories |
| D07 | Implementation | Enterprise implementation-approach methodology |
| D08 | Agent Framework | agent naming, schema, manifest, indexing, context protection |
| D09 | Session & Budget | Token budget, session state, requirements tracking |
| D10 | Dev Protocol | Self-development: requirements, changelog, verification history |
| D11 | Energy & Utilities Domain | PL energy dictionary + Domain (central market info system) — messages, processes, IRiESP, SWI, TSKB |

See [`02_KB_AREAS.md`](02_KB_AREAS.md) for the full file-by-file mapping.

## 6. Key design principles (paraphrased from the master prompt)

1. **Context-window is sacred** — never dump tool source code or large KBs into the context. Tools go to disk; KBs go to Python variables; only small instructions/indexes and final answers enter context.
2. **L0 → L1 → L2 routing** is mandatory — never load an entire large file and never skip the index.
3. **Budget caps per query complexity** — Simple 20 K / Medium 40 K / Complex 60 K tokens of L2 max.
4. **Confidence is graded** — every answer states a confidence tier (✅ VERIFIED · 🟡 VERY LIKELY · 🟠 LIKELY · 🔴 UNVERIFIED) based on freshness × source tier (T1 official → T4 unclear).
5. **Conflict priority** — `Project > Domain > Technology > Common`. Cross-area conflicts are surfaced to the user.
6. **Dual-platform** — the same `kb/` tree runs both on **Host-A** (Python code interpreter, ZIP) and on **GitHub Copilot** (folder + `read_file` + terminal).
7. **Changelog is non-negotiable** — every file write must call `track_change(...)` and every release must `flush_to_changelog` (L1 summary + L2 diffs) before `release_version` is allowed.
8. **Modules, not ad-hoc scripts** — all operations go through `kb/` tools; no one-off code.

## 7. Versioning scheme

`MAJOR.MINOR.PATCH`

- **MAJOR** — architectural restructure / breaking changes
- **MINOR** — prompt or behavioural changes
- **PATCH** — new tools, bug fixes, requirement completions

Each release bundles: author, rationale, structured `changes[]` list (action/target/description/rationale), L1 summary in `Dev_KB_Changelog.json`, and L2 diffs in `kb/dev/changelog/v{X}_details.json`.

## 8. What the host model is expected to do at session start

1. Copy `MasterPrompt.md` into its Instructions field (one-time setup).
2. On every new chat:
 ```python
 exec(open("/mnt/data/Core_Tool_StartupLoader.py").read)
 result = load_startup_resources("<path>/AgentDefinition.zip")
 # Then: print result.context values to conversation
 # Never print result.variables — they stay in Python memory
 ```
3. Follow `§4. CONVERSATION PROTOCOL` from the master prompt:
 classify the question → fast-path or route via L0/L1/L2 → answer in the appropriate response schema (Q&A · DIAG · GEN · DEV).

Continue to [`01_RUNTIME.md`](01_RUNTIME.md) for the resource-loading mechanics in detail.
