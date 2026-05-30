# KB Areas — Full Tool / Instruction / Knowledge Catalog

This file is the inventory: every Python tool, instruction, KB file and template in the package, grouped by area and (where relevant) by domain.

> Sources: `Core_Index_L1.md` (per-domain listing), `agent_manifest.json` (raw inventory), folder walk of `kb/`.
> Sizes are taken from the L1 index. Counts: **93 resources**, **6,546 KB** total. Areas: business/energy (1), common (22), core (26), dev (6), dev/salesforce (1), tech/salesforce (34). Types: Index (4), Instr (27), KB (12), Tmpl (2), Tool (48).

---

## 1. `kb/core/` — agent identity + framework (the "engine")

Subdivided into four functional groups plus the loose Core_* files at the area root.

### 1.1 Root of `kb/core/`

| File | Type | Size | Role |
|-------------------------------------|-------|------:|-----------------------------------------------------------------|
| `Core_Index_L0.md` | Index | small | Nano routing index — keyword/entity → domain |
| `Core_Index_L1.md` | Index | ~15 K | Quick index — per-domain listing of tools/instr/KB |
| `Core_Instr_Master_Ext.md` | Instr | 2.5 K | Master-prompt extension; G1–G5/V1/DEV menu codes; changelog rule|
| `Core_Instr_ContextProtection.md` | Instr | 3.8 K | Always-active context discipline (§6) |
| `Core_Instr_DualPlatform.md` | Instr | ~5 K | Host-A vs Copilot runtime differences |
| `Core_Instr_IndexWeighting.md` | Instr | — | Rules for weighting index entries during rebuild |
| `Core_Tool_StartupLoader.py` | Tool | 14.2 K| Bootstrap; manifest → typed routing → context/var/disk |
| `Core_Tool_StorageAdapter.py` | Tool | — | Monkey-patches `zipfile.ZipFile` for ZIP↔folder transparency |

### 1.2 `kb/core/AgentFactory/` — generation pipeline (D01)

| File | Type | Size | Role |
|--------------------------------------|-------|-------:|---------------------------------------------------------------|
| `Core_Instr_GenerationRecipe.md` | Instr | 4.4 K | 6-step recipe (Platform → Bootstrap → Parse → Identity → Package → Verify) |
| `Core_Tool_Bootstrap.py` | Tool | 6.4 K | Stage-1 runtime bootstrap |
| `Core_Tool_AgentGen.py` | Tool | 40.1 K | Unified generator |
| `Core_Tool_IdentityGen.py` | Tool | 30.9 K | Builds MasterPrompt + menu + indexes for the new agent |
| `Core_Tool_AgentPackager.py` | Tool | 29.6 K | Assembles Factory + project KB + identity → ZIP or folder |
| `Core_Tool_TemplateGen.py` | Tool | 9.6 K | Template scaffolding |

### 1.3 `kb/core/Agent Extension/` — maintenance & dev tooling (D02 + D08 + D10)

| File | Type | Size | Role |
|---------------------------------|-------|-------:|-------------------------------------------------------------------|
| `Core_Instr_DevProtocol.md` | Instr | 6.0 K | DEV-mode rules: requirement lifecycle, changelog enforcement, merge protocol |
| `Core_Tool_Verify.py` | Tool | 80.5 K | Release pipeline & regression testing |
| `Core_Tool_KBManifest.py` | Tool | 21.4 K | Manifest builder / validator |
| `Core_Tool_KBSchema.py` | Tool | 17.3 K | Resource schema & naming-convention enforcement |
| `Core_Tool_AgentExtend.py` | Tool | 23.3 K | "Custom Agent Modules" (REQ-039) |
| `Core_Tool_Repair.py` | Tool | 14.1 K | Auto-repair engine |
| `Core_Tool_Report.py` | Tool | 12.6 K | Feedback report generator |
| `Core_Tool_ChangelogWriter.py` | Tool | — | Auto-tracker + L1/L2 writer + compare_zips/merge_versions |

### 1.4 `kb/core/Indexing/` — index machinery (D08)

| File | Type | Size | Role |
|-----------------------------------|-------|------:|---------------------------------------------------------------|
| `Core_Instr_IndexingSystem.md` | Instr | 6.0 K | L0/L1/L2 specification + generation rules |
| `Core_Tool_RebuildIndexes.py` | Tool | 51.6 K| Rebuild L0/L1 + manifest from ZIP; `analyze_project_knowledge`, `optimize_domain_structure` |
| `Core_Tool_DomainStub.py` | Tool | 2.5 K | Domain-logic stub |
| `Core_Tool_RuntimeHelpers.py` | Tool | 3.6 K | Common ZIP + Markdown access helpers (`load_from_zip`, `load_section`, `search_in_zip`, `list_zip`, `is_loaded`) |

### 1.5 `kb/core/Menu & Config/` — UX surface (D08)

| File | Type | Size | Role |
|--------------------------------------------|-------|-------:|------------------------------------------------------------|
| `Core_INSTR_Menu.md` | Instr | 1.4 K | Text menu with G1–G5, ASK, DOC, SF1–SF5, DEV, MERGE, V1 |
| `Core_KB_ConfiguratorRegistry.json` | KB | 10.8 K | Registry of configurator options |
| `Core_Tool_Config.py` | Tool | 1.6 K | Agent configuration system |
| `Core_Tool_AgentMenuBuilder.py` | Tool | 34.9 K | Dynamic menu builder — `load_menu`, `load_light_config`, `load_heavy_config` |
| `Core_Tool_AgentConfigurator_light.jsx` | Tool | 7.9 K | Simple GUI configurator (React artifact) |
| `Core_Tool_AgentConfigurator_heavy.jsx` | Tool | 32.5 K | Full GUI configurator |

## 2. `kb/common/` — shared reusable tools (D04 + D05 + D06 + D07 + D09)

Tools and instructions that apply across any technology or domain. Most ship in their own sub-folder with `Common_Instr_*` alongside `Common_Tool_*` so the LLM has both the *how-to-use* and the *executable*.

### 2.1 Root of `kb/common/` — session/budget/requirements/tracker (D09)

| File | Type | Size | Role |
|-------------------------------|------|-------:|-----------------------------------------------------|
| `Common_Tool_Budget.py` | Tool | 3.5 K | Token-budget tracker |
| `Common_Tool_Requirements.py` | Tool | 14.4 K | Structured requirements engine |
| `Common_Tool_Session.py` | Tool | 10.4 K | Next-session generator (handoff) |
| `Common_Tool_Tracker.py` | Tool | 9.8 K | Activity tracker |

### 2.2 `Azure Dev Ops import/` (D05)

| File | Type | Size | Role |
|-----------------------------------|-------|-------:|---------------------------------------------------|
| `Common_Instr_AdoDataReader.md` | Instr | 5.7 K | How the agent reads ADO exports |
| `Common_Tool_AdoCsvCleaner.py` | Tool | 37.7 K | ADO Work-Item CSV → LLM-ready format |

### 2.3 `Confluence import/` (D05)

| File | Type | Size | Role |
|-------------------------------------|-------|-------:|-----------------------------------------------------|
| `Common_Instr_ConfluenceDataReader.md` | Instr | 9.7 K | How to read Confluence dumps |
| `Common_Instr_ConfluenceToKb.md` | Instr | 7.7 K | Pipeline instructions |
| `Common_Tool_ConfluenceToKb.py` | Tool | 65.6 K | Confluence branch (PAT auth) → markdown KB |

### 2.4 `EA BPMN import/` (D05)

| File | Type | Size | Role |
|---------------------------------|-------|-------:|-------------------------------------------------------|
| `Common_Instr_EaDataReader.md` | Instr | 12.0 K | How to read EA diagram data |
| `Common_Instr_EaXmiToLlm.md` | Instr | 9.3 K | XMI → LLM tool instructions |
| `Common_Tool_EaXmiToLlm.py` | Tool | 53.5 K | Enterprise Architect XMI → LLM-readable BPMN process descriptions (v3.0) |

### 2.5 `Implementation Approach/` (D07)

| File | Type | Size | Role |
|---------------------------------|-------|-------:|---------------------------------------------------|
| `Common_Instr_ImplApproach.md` | Instr | 8.9 K | Methodology guide |
| `Common_Tool_ImplApproach.py` | Tool | 46.5 K | Implementation-approach generator |

### 2.6 `Doc Generator/` (D04)

| File | Type | Size | Role |
|-------------------------------------|-------|-----------:|------------------------------------------------------------|
| `Common_Instr_DocGen.md` | Instr | 7.0 K | Plan-driven DOCX/PPTX generation system prompt |
| `Common_Instr_MdFormatDocGen.md` | Instr | 5.3 K | Markdown-formatting rules for analytical .md inputs |
| `Common_Tool_DocGen.py` | Tool | 38.7 K | DOCX generator v3.3 (TOC, sections, tables, quotes, timing report) |
| `Common_Tool_PptGen.py` | Tool | 14.6 K | branded PPTX generator v1.0 |
| `Common_Tmpl_DocxTemplate.docx` | Tmpl | 242.5 K | Branded Word template |
| `Common_Tmpl_PptxTemplate.pptx` | Tmpl | 4 543.6 K | Branded PowerPoint template |

### 2.7 `User Story/` (D06)

| File | Type | Size | Role |
|---------------------------------|-------|-------:|---------------------------------------------------|
| `Common_Instr_UserStories.md` | Instr | 7.4 K | User-story writing guide |
| `Common_Tool_UserStories.py` | Tool | 52.5 K | User-stories generator v3.0 |

## 3. `kb/tech/salesforce/` — Salesforce stack (D03)

This is the largest single area — 34 resources, ~700 KB of tools alone, plus 11 fine-grained code-gen instruction files.

### 3.1 Top-level SF tools

| File | Type | Size | Role |
|--------------------------------------------|-------|--------:|-----------------------------------------------------------------|
| `Tech_SF_Tool_Pull.py` | Tool | 57.3 K | Parse `.object` XML → all metadata components → Excel data model|
| `Tech_SF_Tool_Push.py` | Tool | 27.9 K | Validate Excel diff → SFDX deployment package |
| `Tech_SF_Tool_Diff.py` | Tool | 18.4 K | Compare two field lists / orgs → detailed diff |
| `Tech_SF_Tool_Audit.py` | Tool | 113.4 K | Load all Apex classes/triggers from metadata ZIP; quality rules |
| `Tech_SF_Tool_Schema.py` | Tool | 13.5 K | SF metadata schema definitions |
| `Tech_SF_Tool_Excel.py` | Tool | 17.5 K | Filter headers / widths / dropdowns by config |
| `Tech_SF_Tool_Styles.py` | Tool | 1.9 K | Visual styles for Excel output |
| `Tech_SF_Tool_IO.py` | Tool | 19.9 K | ZIP path normalisation + I/O helpers |
| `Tech_SF_Tool_Config.py` | Tool | 4.3 K | Resolve excel config with defaults |
| `Tech_SF_Tool_Budget.py` | Tool | 10.6 K | Token budget tracker for SF agents |
| `Tech_SF_Tool_Help.py` | Tool | 47.6 K | Complete reference for all SF tools |
| `Tech_SF_Tool_Menu.py` | Tool | 41.8 K | Interactive menu for the SF agent |
| `Tech_SF_Tool_Knowledge.py` | Tool | 37.3 K | Knowledge reader for SF Data Model Agent (REQ-033) |
| `Tech_SF_Tool_AgentGen.py` | Tool | 140.2 K | Business Agent Generator for Salesforce orgs (REQ-038 + REQ-039)|
| `Tech_SF_Tool_CopilotAgentGen.py` | Tool | 28.8 K | GitHub Copilot Agent generator for SF (G5) |
| `Tech_SF_Tool_ConfiguratorCompact.jsx` | Tool | 11.8 K | Compact GUI configurator |

### 3.2 SF indexes + knowledge base

| File | Type | Size | Role |
|-----------------------------------|-------|----------:|----------------------------------------------------------------|
| `Tech_SF_Index_FieldIndex.md` | Index | 1.1 K | Field-level index |
| `Tech_SF_Index_ModelSummary.md` | Index | 1.1 K | Data-model summary |
| `Tech_SF_KB_ApexCode.md` | KB | 1.8 K | Apex classes catalogue |
| `Tech_SF_KB_Triggers.md` | KB | 1.3 K | Triggers catalogue |
| `Tech_SF_KB_DevStandards.md` | KB | 46.2 K | Comprehensive SF development standards |
| `Tech_SF_KB_CopilotAgentTemplate.zip` | KB | 164.9 K | Template ZIP with 52 instructions + 12 skills + standards (G5) |

### 3.3 `Code Generation/` (sub-folder)

Master instruction (`Tech_SF_Instr_CodeGen.md`) + 8 focused sub-instructions selected on demand based on the request type:

| File | Topic |
|-----------------------------------------------|--------------------------------------------------------------------|
| `Tech_SF_Instr_CodeGen.md` | Master code-gen guide; references sub-instructions and standards |
| `Tech_SF_Instr_CodeGen_Apex.md` | Apex classes, triggers, TAF, batch/queueable/schedulable |
| `Tech_SF_Instr_CodeGen_CodeReview.md` | Review rubric, scoring, PR templates |
| `Tech_SF_Instr_CodeGen_DataModel.md` | Objects, fields, relationships, ERD, migration |
| `Tech_SF_Instr_CodeGen_Flow.md` | Flow XML, bulk-safety best practices |
| `Tech_SF_Instr_CodeGen_LWC.md` | Lightning Web Components (HTML/JS/CSS) |
| `Tech_SF_Instr_CodeGen_SOQL.md` | Query optimisation, selective queries |
| `Tech_SF_Instr_CodeGen_Security.md` | FLS/CRUD, sharing, auth, CSP |
| `Tech_SF_Instr_CodeGen_Testing.md` | Test classes, mocking, bulk tests, coverage targets |
| `Tech_SF_Instr_CodeGen_Explain.md` | Code-explanation guide |
| `Tech_SF_Tool_CodeGen.py` (27.3 K) | Salesforce Code Generation, Review & Explanation Module v1.1.0 |

### 3.4 `Implementation Approach/`

| File | Type | Size | Role |
|-------------------------------------|-------|-------:|---------------------------------------|
| `Tech_SF_Instr_ImplApproach.md` | Instr | 5.4 K | SF-specific impl-approach guide |
| `Tech_SF_Tool_ImplApproach.py` | Tool | 43.2 K | SF Impl-Approach module v1.0 |

### 3.5 `User Story/`

| File | Type | Size | Role |
|---------------------------------|-------|-------:|-------------------------------------|
| `Tech_SF_Instr_UserStories.md` | Instr | 4.9 K | SF user-story writing guide |
| `Tech_SF_Tool_UserStories.py` | Tool | 37.1 K | SF user-stories module v1.0 |

## 4. `kb/bus/` — business-domain knowledge (D11)

### 4.1 `kb/bus/energy/` — PL energy dictionary

| File | Type | Size | Role |
|-------------------------------|------|--------:|------------------------------------------------------------|
| `Bus_ENRG_KB_PL_Dictionary.md`| KB | 22.4 K | Polish energy-sector term dictionary |

### 4.2 `kb/bus/enrg/` — Domain (central market information system)

A complete domain KB for PL's central energy-market info system: 14 files, mostly stored in Python variables because they range from 100 KB to ~20 MB.

**Document hierarchy (priority on conflicts: IRiESP > SWI > TSKB):**
```
IRiESP-OIRE (transmission-grid operation, 271 pp.)
 └── SWI (Annex 1 — business processes, 100 pp.)
 └── TSKB (technical "how")
 ├── UNK messages (XML/XSD) ......... Messages.json
 ├── Data types (enums) ............. DataTypes.json
 ├── Error / acceptance codes ....... ErrorCodes.json
 ├── Update categories .............. UpdateCategories.json
 ├── Priority matrix ................ PrioMatrix.json
 ├── Process catalogue (11/53) ...... Processes.json
 └── TSKB main (sections 1–11) ...... TSKBMain.json
Scenarios (auxiliary) ......................... Scenarios.json
```

**Instructions (4):**

| File | Type | Size | Role |
|-----------------------------------------------|-------|-------:|----------------------------------------------------------|
| `Bus_ENRG_Instr_Domain_AgentPrompt.md` | Instr | 17 K | How an agent should use the Domain KB |
| `Bus_ENRG_Instr_Domain_DocumentationGuide.md` | Instr | 14 K | SWI → TSKB → appendix routing |
| `Bus_ENRG_Instr_Domain_IndexHints.md` | Instr | 7 K | Index hints for re-build / agent generation |
| `Bus_ENRG_Instr_Domain_VerificationPolicy.md` | Instr | 2 K | Data-quality verification rules |

**Knowledge bases (12 JSON):**

| File | Size | Content |
|-----------------------------------------------|----------:|-------------------------------------------------------------|
| `Bus_ENRG_KB_Domain_Catalog.json` | 11 K | Artefact catalog, schemas, query examples |
| `Bus_ENRG_KB_Domain_Messages.json` | ~20 MB | 135 UNK messages, 6483 elements (PL-codes, BDT, enums, obligation rules) |
| `Bus_ENRG_KB_Domain_DataTypes.json` | 276 K | 164 data types, 108 enums with CK-code values |
| `Bus_ENRG_KB_Domain_CrossRef.json` | 4.5 MB | 5 reverse indexes (PL-code, section, datatype, element, obligation) |
| `Bus_ENRG_KB_Domain_ErrorCodes.json` | 183 K | 481 error codes (CE/CA/CL/CS) with resolution guidance |
| `Bus_ENRG_KB_Domain_Processes.json` | 571 K | Process definitions groups 1–11 |
| `Bus_ENRG_KB_Domain_ProcessDetails.json` | 569 K | Detailed flows, steps, actors, message sequences |
| `Bus_ENRG_KB_Domain_Scenarios.json` | 194 K | Business scenarios with process combinations |
| `Bus_ENRG_KB_Domain_PrioMatrix.json` | 672 K | Priority matrix for processes / messages |
| `Bus_ENRG_KB_Domain_UpdateCategories.json` | 170 K | Update categories |
| `Bus_ENRG_KB_Domain_SWI.json` | 193 K | SWI standard (URE-approved, 100 pp.) — 🟢 T1 |
| `Bus_ENRG_KB_Domain_IRiESP.json` | 823 K | IRiESP regulation (URE-approved, 271 pp.) — 🟢 T1 |
| `Bus_ENRG_KB_Domain_TSKBMain.json` | 109 K | TSKB main structure & metadata |
| `Bus_ENRG_KB_Domain_VerificationRegistry.json` | 8 K | Verification registry |

**Parsers (2):**

| File | Type | Size | Role |
|--------------------------------------------|------|------:|---------------------------------------------------------|
| `Bus_ENRG_Tool_Domain_ProcessParser.py` | Tool | 12 K | Parser for Domain process definitions |
| `Bus_ENRG_Tool_Domain_TSKBParser.py` | Tool | 22 K | TSKB XSD + PDF → JSON parser (sections 1-7 XSD, 8 PDF) |

**Query routing hints** (excerpt from L1):

| Question | Subsection | Primary file(s) |
|-----------------------------------------------|------------|-------------------------------------------|
| "Jak wygląda komunikat X?" | D11.2 | Messages.json |
| "Jakie wartości ma pole Y?" | D11.2 | DataTypes.json |
| "Gdzie występuje PL-xxx?" | D11.2 | CrossRef.json |
| "Jaki jest proces X?" | D11.3 | Processes.json, ProcessDetails.json |
| "Co mówi SWI o…?" / "Co mówi IRiESP o…?" | D11.4 | SWI.json / IRiESP.json |

## 5. `kb/dev/` — agent's own development state (D10)

This is the agent's *self*-knowledge: what was changed, what's planned, what's verified.

| File | Type | Size | Role |
|---------------------------------------|------|--------:|-----------------------------------------------------------------|
| `Dev_KB_Changelog.json` | KB | 11.1 K | L1 changelog summary — one entry per version (author/rationale/changes[]) |
| `changelog/v1.0.17_details.json` … `v1.0.25_details.json` | KB | — | L2 per-version detail files with full unified diffs |
| `Dev_KB_RequirementsDB.json` | KB | 0.7 K | Structured requirements DB |
| `Dev_KB_RequirementsMD.md` | KB | 0.4 K | Human-readable backlog |
| `DEV_KB_Chronicle.md` | KB | 1.1 K | Narrative version chronicle |
| `Dev_KB_ApiBaseline.json` | KB | 64.8 K | Pinned API baseline — used by `Core_Tool_Verify.py` for regression tests |
| `Dev_KB_VerifyHistory.json` | KB | 0.3 K | Past verification runs |
| `Dev_SF_KB_DynamicContext.md` | KB | 3.0 K | Auto-generated by `sf_verify.py` — current SF-related context |

## 6. Quick cross-reference — Domain → primary files

| Domain | Primary tools | Primary instructions | Primary KB |
|--------|----------------------------------------------------------------------------------------------|---------------------------------------------------------------|-------------------------------------|
| D01 | AgentGen, Bootstrap, IdentityGen, AgentPackager, TemplateGen, CopilotAgentGen, Configurators | GenerationRecipe | ConfiguratorRegistry, CopilotAgentTemplate.zip |
| D02 | Verify, Repair, Report, KBManifest, KBSchema, AgentExtend | DevProtocol (also D10) | VerifyHistory |
| D03 | Pull, Push, Diff, Audit, CodeGen, Knowledge, AgentGen, Menu, Help, Excel, Schema, Styles, IO, Budget, Config, ImplApproach, UserStories, CopilotAgentGen, ConfiguratorCompact | 11 CodeGen_* + ImplApproach + UserStories | DevStandards, ApexCode, Triggers, FieldIndex, ModelSummary |
| D04 | DocGen, PptGen | DocGen, MdFormatDocGen | DocxTemplate, PptxTemplate |
| D05 | AdoCsvCleaner, ConfluenceToKb, EaXmiToLlm | AdoDataReader, ConfluenceDataReader, ConfluenceToKb, EaDataReader, EaXmiToLlm | — |
| D06 | Common_Tool_UserStories | Common_Instr_UserStories | — |
| D07 | Common_Tool_ImplApproach | Common_Instr_ImplApproach | — |
| D08 | StartupLoader, StorageAdapter, RebuildIndexes, RuntimeHelpers, DomainStub, MenuBuilder, Config | ContextProtection, IndexingSystem, IndexWeighting, Master_Ext, DualPlatform, Menu | — |
| D09 | Budget, Tracker, Session, Requirements | — | — |
| D10 | ChangelogWriter (in Agent Extension) | DevProtocol | Changelog, RequirementsDB/MD, Chronicle, ApiBaseline, VerifyHistory |
| D11 | Domain_ProcessParser, Domain_TSKBParser | Domain_AgentPrompt, DocumentationGuide, IndexHints, VerificationPolicy | 14 Domain_* + PL_Dictionary |

Continue to [`03_WORKFLOWS.md`](03_WORKFLOWS.md) for the operational workflows (G1–G5 generation, dev protocol, changelog enforcement, version merge).
