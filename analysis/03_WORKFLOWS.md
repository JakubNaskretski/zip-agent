# Workflows — Generation, Dev, Changelog, Merge

This file documents the end-to-end *operations* the Agent Factory performs. Each workflow is a sequence of tool calls + checkpoints; the agent never improvises — it follows the recipes shipped in `kb/core/`.

> Source files driving this layer:
> - `MasterPrompt.md` §10 (Changelog Auto-Tracking) and §11 (Version Merge Detection)
> - `kb/core/Core_Instr_Master_Ext.md`
> - `kb/core/AgentFactory/Core_Instr_GenerationRecipe.md`
> - `kb/core/Agent Extension/Core_Instr_DevProtocol.md`
> - `kb/core/Core_Instr_DualPlatform.md`

---

## 1. Operational menu (entry points)

From `Core_INSTR_Menu.md` — the user-facing command surface:

| Code | Operation | Notes |
|--------|---------------------------------|---------------------------------------------------------------|
| `G1` | Lite agent | Core + Common only |
| `G2` | SF Tech agent | Salesforce, no project data |
| `G3` | SF Project agent | Salesforce + `metadata.zip` |
| `G4` | Custom agent | Interactive configurator (heavy JSX) |
| `G5` | SF Copilot agent | Native GitHub Copilot agent for SF |
| `ASK` | Question | Query KB via L0→L1→L2 |
| `DOC` | document | DOCX / PPTX from markdown |
| `SF1` | Pull → Excel | Metadata XML → editable Excel |
| `SF2` | Push → Package | Excel → SFDX deployment ZIP |
| `SF3` | Metadata → KB | Parse to Org Knowledge (`.md`) |
| `SF4` | Diff / Compare | Compare orgs or snapshots |
| `SF5` | Quality Audit | Evaluate quality rules |
| `DEV` | Extend agent | Add knowledge / capabilities |
| `MERGE`| Version merge | Compare & merge two agent ZIPs |
| `VERIFY`| Verification | Run consistency checks |
| `BUDGET`| Token budget | Context usage report |
| `V1` | Verify against Factory standards | (planned) |

`Core_Tool_AgentMenuBuilder.py` powers all of these — `load_menu`, `load_light_config`, `load_heavy_config` are the entry points from the conversation protocol.

## 2. Agent generation — G1 to G4 (the main pipeline)

### 2.1 Step 0 — confirm target platform ⭐ mandatory

Before any G1–G4 generation the agent **must** ask:

> *"For which platform should I generate the agent: Host-A or GitHub Copilot?"*

The answer becomes `target_platform = "host-a" | "copilot"` and is threaded through IdentityGen + AgentPackager.

| Platform | Output | StartupLoader path |
|------------|-------------------|------------------------------|
| `host-a` | `.zip` file | `exec(open("/mnt/data/..."))`|
| `copilot` | Folder + `.github/agents/` config | `exec(open("./kb/core/..."))` |

### 2.2 Pipeline (`Core_Instr_GenerationRecipe.md` v2.0)

```
STEP 0 Platform → ask user → store target_platform
STEP 1 Bootstrap → extract all tools from Factory ZIP into _AGENT_WORK_DIR,
 install import_alias copies, prepend to sys.path
STEP 2 Parse source (skip if no metadata.zip)
 → pull_core(metadata.zip) # → core dict + stats
 → generate_agent(...) # → _sf_project_kb.zip
STEP 3 Identity → generate_identity(agent_name, version, config, ...)
 → returns dict of MasterPrompt + menu + indexes + dev init
 (per platform — `MasterPrompt §3` differs Host-A vs Copilot)
STEP 4 Package → package_agent(factory_zip, project_kb_zip, identity_files, config,
 output_path, output_format="zip"|"folder",
 metadata_zip, import_alias_map, api_registry)
 → produces AgentDefinition_<project>_v1.0.0(.zip|folder)
STEP 5 Verify → verify_all(result_path) — consistency checks
```

### 2.3 Key rules

- Always **follow the recipe** — do not explore code manually
- Use **`import_alias` from manifest** — do not guess module names
- Use **API signatures from manifest** — do not read tool source
- Start every generated agent at **v1.0**, empty backlog, changelog containing only the generation event
- For Host-A: include `StorageAdapter` (no-op passthrough)
- For Copilot: include `StorageAdapter` (essential — FSBackend)
- Always include user-provided knowledge in the project catalog

### 2.4 Platform output comparison

| Aspect | Host-A (`host-a`) | GitHub Copilot (`copilot`) |
|---------------------|-----------------------------|-----------------------------------------------------|
| Output format | `.zip` file | Folder |
| `kb/` hierarchy | Identical | Identical |
| Extra files | — | `.github/agents/agent.md`, `.github/copilot-instructions.md` |
| Setup guide | Host-A steps | Copilot steps |
| `MasterPrompt §3` | `exec(open("/mnt/data/..."))` | `exec(open("./kb/core/..."))` |

## 3. G5 — Salesforce Developer Copilot Agent (dedicated path)

G5 is a **different pipeline** — it produces a *native* GitHub Copilot agent, not an agent on Copilot.

### 3.1 Differences from G1–G4

| Aspect | G1–G4 on Copilot | G5 |
|-----------------|-------------------------------|-----------------------------------------------------|
| Agent type | agent on Copilot | Native Copilot agent |
| Structure | `kb/` + `.github/` | `.github/` only |
| Routing | L0 → L1 → L2 (Agent indexes) | `skill-router` → skill file → `sf-*.md` instruction|
| Instructions | MasterPrompt + `kb/` content | 52 granular `sf-*.md` in `.github/instructions/` |
| Skills | None | 12 skill files in `.github/skills/` |
| Pipeline | Bootstrap → IdentityGen → Packager (5 steps) | Direct generation (2 steps) |
| MasterPrompt | Adapted for Copilot | Replaced by `sf-developer.agent.md` |

### 3.2 G5 pipeline

```python
# Step 1 — extract generator + template ZIP from the factory
zf.extract("kb/tech/salesforce/Tech_SF_Tool_CopilotAgentGen.py", "/mnt/data")
zf.extract("kb/tech/salesforce/Tech_SF_KB_CopilotAgentTemplate.zip", "/mnt/data")
exec(open("/mnt/data/kb/tech/salesforce/Tech_SF_Tool_CopilotAgentGen.py").read)

# Step 2 — generate (generic or project mode)
result = generate_copilot_agent(
 output_path="/mnt/data/sf-developer-copilot-agent.zip",
 mode="generic", # or "project" with metadata_path + org_name
 instruction_source_zip="/mnt/data/kb/tech/salesforce/Tech_SF_KB_CopilotAgentTemplate.zip",
)
print_copilot_summary(result)
```

Output: ZIP containing the full `.github/` folder (agent + 52 instructions + 12 skills + standards + config). User extracts to repo root, pushes to GitHub, then uses `@sf-developer` in Copilot Chat.

## 4. ASK — KB question answering

This is the bread-and-butter use case. The conversation protocol (§4 of MasterPrompt) defines the algorithm:

```
Step 0 CLASSIFY — question type (Q&A / GEN / DIAG / DEV), confidence target, domains, complexity
Step 1 FAST-PATH — answer from already-loaded context? general knowledge? single targeted lookup?
 If FAST-PATH triggers, also ask the user whether to deepen the search.
Step 2 ROUTE via L0 — keyword/entity → domain ID(s)
Step 3 PLAN via L1 — domain → specific files + sections + token cost estimate
Step 4 LOAD L2 — load ONLY the sections identified in step 3
Step 5 SYNTHESIZE — answer in the response schema for the question type
```

**Response schemas** (from `MasterPrompt.md`):

- **Q&A CONCISE** — `📋 type | confidence | domains` / `💡 answer` / `⚠️ caveats` / `📚 sources`
- **Q&A FULL** — adds `🚧 constraints` and `🔄 next steps`
- **DIAG** — `🔍 hypotheses` / `🛠️ diagnostic steps` / `💡 resolution` / `🚧 caveats` / `🔄 next steps`
- **GEN** — `📋 type | domains` / `📝 artefact (complete, in specified format)` / `📚 sources` / `✅ compliance checklist`
- **DEV** — `📋 type` / `⚓ baseline version` / `📦 output version` / `📝 changelog` / `✅ checklist` / `🔄 next steps`

Every response footer reports timing + budget:
```
🕐 Generated at hh:mm UTC | Duration X s | 🟢 Budget: N% used (X.X K / Y K tokens) | M interactions
```

## 5. DOC — document generation

Plan-driven DOCX/PPTX generation via `Common_Tool_DocGen.py` + `Common_Tool_PptGen.py` against branded templates.

```python
from doc_gen import generate_from_template # extracted from kb/common/...

plan = {
 "title": "...",
 "subtitle": "...",
 "toc_title": "Table of Contents",
 "toc_entries": [("TOC1", "Section Name"), ...],
 "sections": [
 {"title": "Section 1", "content": "...", "level": 1},
 {"title": "1.1", "content": "", "level": 2, "bullets": ["a", "b"]},
 {"type": "table", "headers": ["A", "B"], "rows": [["1","2"]], "caption": "Table 1"},
 {"type": "quote", "text": "..."},
 ],
}
generate_from_template(plan, "/mnt/data/output.docx")
```

`read_and_generate(input_path, plan, output_path)` (v3.3) is a one-shot pipeline that reads a source file and emits the DOCX with a per-stage timing report.

## 6. DEV — extending the agent (the development protocol)

DEV mode activates `Core_Instr_DevProtocol.md` (held in a Python variable on startup, printed to context only when DEV is engaged).

### 6.1 Core invariants (non-negotiable)

1. Identify and name user requirements.
2. Always enable `Common_Tool_Requirements.py` (requirement mgmt) and `Common_Tool_Budget.py` (token budget).
3. Check existing logic / backlog / changelog for similar changes before building. Inform the user about overlaps.
4. Tell the user the change is **session-only** until a new agent definition is built.
5. Modules, not ad-hoc scripts — every operation uses `kb/` tools.
6. Classify the change (area + type) before building.
7. Warn at **70 %** of context budget; **session handoff** above **90 %**.

### 6.2 Requirement lifecycle

```
❌ TODO → 🔧 PARTIAL → 🧪 DONE-GEN → ✅ DONE-SCRIPT → VERIFIED → RELEASED → SUPERSEDED
```

Each requirement carries: ID, title, status, priority (P0–P3), effort (S/M/L/XL), acceptance criteria, module mapping, dependencies, history.

Stored in:
- `kb/dev/Dev_KB_RequirementsDB.json` (source of truth)
- `kb/dev/Dev_KB_RequirementsMD.md` (human-readable)
- `kb/dev/Dev_KB_Changelog.json` (history)

### 6.3 Development workflow

```
1. REQUIREMENT → Define REQ + acceptance criteria
2. CLARIFICATION → Confirm criteria
3. PoC → Execute, confirm result
4. ONE-TIME GATE → If single-session only: stop here (insist on continuation for KB)
5. IMPLEMENT → Build modular tool; create scripts + .md in correct location
6. BUILD_TEST → Test cases co-located with the module
7. UPDATE_CORE → Update manifest + indexes (Knowledge Index Optimization Protocol)
8. UPDATE_UX → Menu / configurator / help (if needed)
9. CHANGELOG → flush_to_changelog — L1 summary + L2 details with diffs
10. VERIFY → Verification protocol
11. RELEASE → pre_release_check.ok → bump version → build ZIP
```

### 6.4 Agent Factory Extension protocol (for child agents only)

If a *child* agent (not the Factory itself) needs a cross-cutting capability, it builds a `{changeName}_AgentExt_{date}.zip` containing:
- a short `.md` summary, requirements and rationale, implementation guidelines
- the created / modified files

and asks the user to send it to the Agent dev team for inclusion in a future Factory release.

## 7. CHANGELOG — auto-enforcement (NON-NEGOTIABLE)

§10 of MasterPrompt makes this active for **every** session, not just DEV.

### 7.1 Whenever a file is added / updated / removed

Call `track_change(action, target, description, rationale)` immediately:

```python
track_change(
 action="UPDATED", # or ADDED | REMOVED | FIXED | DESIGN
 target="Core_Tool_ChangelogWriter.py",
 description="Added validate_rationale to enforce non-empty rationale before release.",
 rationale="REQ-CHANGELOG-ENFORCE",
)
```

### 7.2 Layered changelog architecture

| Layer | File | Content |
|-------------|---------------------------------------------|------------------------------------------------------|
| L1 Summary | `Dev_KB_Changelog.json` | Per-version entry: author, timestamp, rationale, `changes[]`, stats, pointer to L2 |
| L2 Details | `kb/dev/changelog/v{X}_details.json` | Per-file metadata + full unified diffs |

L2 is generated **automatically** by `Core_Tool_ChangelogWriter.py`. Layer-1 `details_file` field must point to its Layer-2 file. Format-version 2; legacy entries preserved as-is.

### 7.3 Pre-release validation gates

`release_version` blocks the release if any of:

| Check | Condition | Result |
|--------------|---------------------------------|-------------------------------------|
| Author | empty / whitespace | ❌ BLOCK — ask user |
| Rationale | empty / whitespace | ❌ BLOCK — ask user |
| Changes | empty list | ❌ BLOCK — at least one required |
| Changes entry| missing action/target/description | ⚠️ WARN + auto-fix attempt |
| Diffs | text files changed without diff | ⚠️ WARN |

### 7.4 The release ritual

```
SESSION START:
 set_session_author("Name") # ask user if unknown
 set_session_rationale("Why") # ask user before first change

DURING DEVELOPMENT (after EVERY file write/modify/delete):
 track_change("UPDATED", "filename.py", "Detailed description", "Reason")

BEFORE RELEASE:
 check = pre_release_check # must return ok=True
 flush_to_changelog(zip, version, description) # writes L1+L2 atomically
 release_version(...) # only proceeds if changelog is valid
```

Vague descriptions are **unacceptable** (`"Updated file.py"`, `"Bug fix"`, `"Various improvements"`). The agent must reconstruct a precise change entry from the diff if it forgot to track.

## 8. MERGE — version merge protocol (v1.0.21+)

§11 of MasterPrompt — triggered when a different version is uploaded mid-session.

### 8.1 Flow

```
1. DETECT → compare manifest versions (old vs new)
2. COMPARE → compare_zips(old_zip, new_zip) — full diff analysis
3. REPORT → generate_merge_report(comparison) — show to user
4. REVIEW → user inspects added / modified / removed files + diffs
5. CONFIRM → user explicitly approves the merge
6. MERGE → merge_versions(old_zip, new_zip, author, rationale)
7. VERIFY → run verification on merged ZIP
8. ACTIVATE → new ZIP becomes the active session ZIP
```

### 8.2 Non-negotiable merge rules

1. **NEW ZIP is the base** — its content wins on collision
2. **Old changelog entries are NEVER deleted** — only added to
3. **Old L2 detail files are ALWAYS preserved** — copied if missing
4. Conflicts (same version, different content) → old entry kept with `-pre-merge` suffix
5. A merge-itself changelog entry is **always** generated (with stats + per-file diffs, `merge_type: "version_merge"` in L2)
6. **Author + rationale are required** — merge blocks without them
7. **User must see the report before merge executes** — no silent merges

```python
from Core_Tool_ChangelogWriter import compare_zips, generate_merge_report, merge_versions
comparison = compare_zips(old_zip, new_zip)
print(generate_merge_report(comparison)) # → show to user
result = merge_versions( # → after explicit user confirm
 old_zip_path=old_zip,
 new_zip_path=new_zip,
 author="...",
 rationale="...",
 merge_version=None, # uses new manifest version if None
 dry_run=False, # True for preview only
)
```

## 9. VERIFY — release pipeline & regression testing

`Core_Tool_Verify.py` (80 K — the second-largest tool in the package). Used:

- At the end of every G1–G5 generation (`verify_all(result_path)`).
- As the standalone `VERIFY` menu command.
- As pre-release gate during DEV.

It checks consistency against `Dev_KB_ApiBaseline.json` and runs the regression suite. History is recorded in `Dev_KB_VerifyHistory.json`.

## 10. Knowledge-index optimisation protocol

Runs after importing any new project knowledge, **before** rebuilding indexes:

```
Step 1 analyze_project_knowledge(zip) # → business topics, entities, processes, tech specifics
Step 2 optimize_domain_structure(zip, analysis)
 – group domains by business functionality (not by file type)
 – L0 keywords must include natural business terms
 – merge tiny domains; split overloaded ones (30+ files in one generic domain)
 – preserve core domains (Agent Framework, Dev Protocol, Session & Budget)
 – project domains appear BEFORE generic technology domains
Step 3 rebuild_all(zip) # uses optimised domain config
```

**When to apply**

| ✅ Apply | ❌ Skip |
|------------------------------------------------------|---------------------------------------|
| After importing project knowledge (SF metadata, Confluence, ADO, manual KB) | Core/Common tool changes (static domains) |
| After adding/removing significant sources | |
| When user reports difficulty finding info via Q&A | |

## 11. Version chronicle (paraphrased from `DEV_KB_Chronicle.md`)

- **v1.0.0** (2026-03-14) — initial official release; baseline consolidates all prior dev history (v3.x–v5.x). 77 resources, 45 tools, 14 instructions, 12 KB articles, 4 indexes, 2 templates. 40 released REQs; backlog 0.
- **v1.0.17 – v1.0.25** — incremental: changelog format v2, auto-enforcement, version-merge protocol, dual-platform support, dual-platform StorageAdapter, knowledge-index optimisation.
- **v1.0.26** (this folder) — current snapshot; manifest still labelled v1.0.23 but folder is v1.0.26. L1 index dated 2026-03-19; L0 dated 2026-04-01.

---

## Appendix — One-page cheat sheet

```
SESSION START (Host-A)
 exec(open("/mnt/data/Core_Tool_StartupLoader.py").read)
 result = load_startup_resources("/mnt/data/AgentDefinition.zip")
 # print result.context only; result.variables stays in Python

GENERATE
 Ask: Host-A or Copilot?
 G1/G2/G3/G4: bootstrap → parse (optional) → identity → package → verify
 G5: extract CopilotAgentGen + template → generate_copilot_agent(...) → done

ASK
 L0 (keyword → Dxx) → L1 (Dxx → file+section) → L2 (load on demand)
 Cap L2 by complexity: 20K / 40K / 60K tokens
 Respond with the schema for the question type
 Footer: time + budget

DEV
 set_session_author + set_session_rationale
 track_change after EVERY file op
 flush_to_changelog → pre_release_check → release_version

MERGE (different ZIP uploaded)
 compare_zips → generate_merge_report → user confirms → merge_versions → verify

CONFLICT PRIORITY
 Project > Domain > Technology > Common

CONFIDENCE
 ✅ VERIFIED | 🟡 VERY LIKELY | 🟠 LIKELY | 🔴 UNVERIFIED
```
