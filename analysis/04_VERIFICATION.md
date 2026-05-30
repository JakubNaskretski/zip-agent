# Mechanic Verification Report — Core Engine

**Scope:** ~12,000 lines of Python across 20 core-engine files (the framework "bones" — everything outside the SF/Domain/Common tool corpus).
**Lens:** the user's stated concerns, in priority order:
1. **Long response times and poor mapping/indexing** ← the main pain
2. Coupling & redundancy
3. Doc ↔ code divergence
4. Context-window efficiency

**Severity model** — every finding in this report is classified under one of three tiers, because "what's a bug" depends on how the framework is actually run:

| Tier | Label | Meaning |
|------|----------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| 🔴 | **Runtime bug** | Actually breaks the agent at runtime (KeyError, wrong output, silent skip of work the agent thinks happened). |
| 🟠 | **Runtime smell** | Works but produces the symptoms the user complains about — poor routing, slow rebuilds, wasted context, bloated output. |
| 🟡 | **Design fragility** | Works in the agent runtime *by design* — the framework assumes every tool is `exec`'d into one shared globals dict per `Core_Instr_DualPlatform.md`. These are not runtime bugs *today*. They block the rewrite into proper, testable Python modules tomorrow. |

The framework's runtime contract is: StartupLoader.py:344 `exec(open(...).read, globals)` makes `_AGENT_WORK_DIR`, `_AGENT_ROOT`, `monkey_patch_zipfile`, and every loaded tool's symbols available to every subsequently-loaded tool. This is intentional. Findings that depend on *breaking* this contract (e.g., calling a tool via `import` instead of `exec`) are classified 🟡, not 🔴.

**Output of this verification:** this top-level doc + 5 component drilldowns:

| Component | Drilldown |
|------------------------------------------------------|--------------------------------------------------------------------------------|
| Indexing (RebuildIndexes, RuntimeHelpers, DomainStub)| [`components/01_indexing.md`](components/01_indexing.md) |
| Generation (AgentGen, IdentityGen, AgentPackager, Bootstrap, TemplateGen, StartupLoader, StorageAdapter) | [`components/02_generation.md`](components/02_generation.md) |
| Maintenance (Verify, ChangelogWriter, KBManifest, KBSchema, AgentExtend, Repair, Report) | [`components/03_maintenance.md`](components/03_maintenance.md) |
| Menu/Config (AgentMenuBuilder, Configurator JSX, ConfiguratorRegistry, Config) | [`components/04_menu_config.md`](components/04_menu_config.md) |
| Manifest & metadata drift forensics | [`components/05_manifest_drift.md`](components/05_manifest_drift.md) |

---

## TL;DR — the eight findings that should drive the rewrite

| # | Tier | Finding | Impact |
|---|------|---------------------------------------------------------------------------------------------------|-----------------------------------------------------------------|
| 1 | 🟠 | **The indexer can't deliver what its spec promises.** `_DOMAIN_KEYWORDS` and `_DOMAIN_ENTITIES` are static hardcoded dicts. No `IndexHints.md` file is ever read. L1 has **no P1–P5 weighting code**. The rich D11 sub-sections you see in `Core_Index_L1.md` are **hand-edited**, not tool output. | Routes questions through a frozen vocabulary that doesn't see new files. Root cause of "poor mapping/indexing". |
| 2 | 🔴 + 🟡 | **The manifest has 4 different writers and 4 different counts of itself.** Top-level array=119, `stats.total_resources`=93, `metadata.resource_count`=98, `indexes.core.resources_at_build`=92. Three version strings, three timestamps, two parallel area-naming systems (`tech` vs `tech/salesforce`, `Bus_ENRG` vs `business/energy`), 8 synonymous `load_strategy` values. | 🔴 Indexes don't list 22 resources, so the agent's L0/L1 routing has blind spots → user-visible "poor mapping". 🟡 Three coexisting schemas block a clean rewrite. |
| 3 | 🔴 + 🟡 | **`release_version` bypasses `pre_release_check`.** DevProtocol step 9→10→11 (flush → check → release) is reversed in code: `release_version` validates its own kwargs (Verify.py:1543-1563) and never consults the auto-tracker. **Separately**, the auto-tracker stores state in Python module globals (`_pending_changes`, `_session_author`, `_session_rationale`) — which works fine inside a single Python session (state persists across model turns) but evaporates on sandbox restart, with no on-disk shadow. | 🔴 Real today: changelog enforcement (MasterPrompt §10 "NON-NEGOTIABLE") is unenforceable — `release_version` accepts kwargs and skips the tracker entirely. 🟡 The module-global state model blocks the rewrite to proper modules. |
| 4 | 🟡 | **AgentGen, TemplateGen, and 686 LOC of dynamic-menu code are unreachable code.** The recipe doesn't call AgentGen (it calls Bootstrap → IdentityGen → AgentPackager). AgentGen imports TemplateGen at L311 — and TemplateGen has a module-scope reference to `_AGENT_WORK_DIR` that would `NameError` on a normal `import`. In the agent runtime where everything is `exec`'d into shared globals, it may load — but it's moot because nothing calls AgentGen and TemplateGen lists 10 pre-v1.0 filenames that no longer exist. | Doesn't fail at runtime because nothing reaches it. ~2000 LOC of context bloat + maintenance load for code paths the agent never executes. |
| 5 | 🔴 + 🟠 | **The Generation Recipe lies about `import_alias` and `api`.** It tells the agent to use `import_alias` to load modules and `api` for signatures. Only **1/119** resources have `import_alias` set; **0/119** have `api`. | 🔴 Bootstrap.py:111 silently skips alias creation for 118 of 119 tools → subsequent `from <alias> import …` calls fail. 🟠 The recipe's "use manifest, don't read source" promise is contract on paper, dead on disk. |
| 6 | 🟠 | **AgentPackager ships every Salesforce file to every generated agent**, regardless of preset. `FACTORY_ONLY_FILES` is the only exclusion list, hardcoded, and **it differs between IdentityGen (28 files) and AgentPackager (23 files)**. The "G1 lite" preset still emits an agent containing `kb/tech/salesforce/*` (just hides it from the menu). | Bloat — generated agents include ~700 KB of irrelevant SF tooling. |
| 7 | 🟠 + 🟡 | **Verify is a 1940-line god-class** with five distinct concerns wedged together (test framework, integrity T1, tool AST T2, instruction sync T3, release pipeline, next-session generator, bolted-on consistency T4). AST is parsed 4× per regression with no memoization. T2.04 `exec`s every Python tool in a sandbox — slow + non-deterministic. | 🟠 Slow regression contributes to long response time on DEV turns; verbose `print` output dominates context. 🟡 Splitting it is a precondition for the rewrite. |
| 8 | 🟡 | **Hidden global namespace coupling everywhere.** `ChangelogWriter.merge_versions` calls undefined `_save_to_zip` (lives in AgentExtend). Verify calls `write_changelog`/`generate_file_details` with no `import`. `Core_Tool_Config.py`, `AgentMenuBuilder.load_light_config/heavy`, `AgentExtend`, `Repair`, `Verify`, `Report` all reference `_AGENT_WORK_DIR` at module scope. **In the agent runtime this all works** — every tool is `exec`'d into the same globals dict by StartupLoader.py:344 per `Core_Instr_DualPlatform.md`'s deliberate design. | Not a runtime bug today. It's the single biggest *blocker* for the rewrite: nothing can be tested outside the conversational sandbox, no module can be moved without rewiring every cross-reference. |

---

## Concerns by category

### 1. Long response times and poor mapping/indexing

| Symptom | Root cause | Drilldown |
|------------------------------------------|------------------------------------------------------------------------------------------------------|-----------|
| Q&A routes to wrong domain | Static `_DOMAIN_KEYWORDS`/`_DOMAIN_ENTITIES` hardcoded in `Core_Tool_RebuildIndexes.py:437/450`. Spec promises "merge from IndexHints + analyzed entities + identity terms" — code doesn't read IndexHints at all. | [01](components/01_indexing.md) |
| Entity → domain map is mush | `Core_Tool_RebuildIndexes.py:794-797` — every project domain gets the **same** global entity list (the per-file filter comment is a lie). | [01](components/01_indexing.md) |
| L1 looks inconsistent (D11 rich, D04 flat)| `_build_l1` (`RebuildIndexes.py:1017-1064`) emits a uniform flat list. **D11's sub-sections are manual hand-edits.** No P1–P5 weighting code anywhere in the file. | [01](components/01_indexing.md) |
| Non-Salesforce projects route to "General" | `TOPIC_KEYWORDS` dict (`RebuildIndexes.py:557-573`) is Salesforce/CRM-flavored. Anything outside that vocabulary falls through. | [01](components/01_indexing.md) |
| Domain IDs renumber between rebuilds | `overlay_prefix` (`RebuildIndexes.py:934`) shifts D-numbers depending on whether project KB exists. References to fixed IDs in docs become stale. | [01](components/01_indexing.md) |
| Index rebuilds are slow | Full re-hash and re-read every run despite `content_hash` being computed (RebuildIndexes.py:252) — never compared against prior manifest. Whole ZIP rewrite every time. `_recalc_stats` runs twice. | [01](components/01_indexing.md) |
| Generation is slow | 2× full factory_zip reads, MD5 + regex-docstring per file, L0/L1 iterates 4 more times, verbose `print` chatter + README dump → tokens. | [02](components/02_generation.md) |
| Regression takes ages | Verify parses every `.py` via `ast.parse` **four times** per run (T2.01, T2.05, T3 registry, baseline). `exec`-the-tool subtest (T2.04) is non-deterministic and slow. No caching. | [03](components/03_maintenance.md) |

### 2. Coupling & redundancy

| Where | What | Drilldown |
|--------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------|-----------|
| RebuildIndexes ↔ AgentPackager | `_classify_resource`, `_discover_domains`, `_build_l0`, `_build_l1`, `_DOMAIN_CLASSIFIERS`, `_DOMAIN_KEYWORDS` duplicated in both files (~600 LOC each). | [01](components/01_indexing.md), [02](components/02_generation.md) |
| Bootstrap ↔ StartupLoader ↔ recipe inline ↔ AgentPackager Phase 1 | Four independent re-implementations of "open ZIP → read manifest → walk names → extract Python tools". Bootstrap uses `importlib`, StartupLoader uses `exec`, with different selection criteria. | [02](components/02_generation.md) |
| Verify ↔ KBSchema ↔ KBManifest | Three places implement naming-convention regex. Verify re-parses KBSchema's source as text instead of importing it (`Verify.py:1907-1924`). | [03](components/03_maintenance.md) |
| Three manifest writers, three schemas | AgentExtend writes v1 shape (`knowledge_sources`/`custom_modules`/`load_strategy=auto`). KBManifest writes v2 (`resources[]`/`load_mode`). A third unknown writer produces the on-disk file with `extract_to_disk`/`read_to_variable` strings in neither vocab. | [03](components/03_maintenance.md), [05](components/05_manifest_drift.md) |
| Verify god-class | 1940 LOC, five concerns. AST parse repeated 4× per run. | [03](components/03_maintenance.md) |
| Menu vocabularies (×3) | `Core_INSTR_Menu.md` codes (G1/SF1/…) vs `_BASE_OPS` (Backlog/Add Req/…) vs `Core_KB_ConfiguratorRegistry.json` taxonomy. No mapping between them. | [04](components/04_menu_config.md) |
| `light.jsx` re-inlines registry JSON | Heavy.jsx imports the real JSON. Light.jsx duplicates it as a literal — guaranteed drift; already drifted (`heavy` reads `kb[i].updated`, `light` has no such field). | [04](components/04_menu_config.md) |
| Hidden global namespace coupling | `ChangelogWriter.merge_versions` calls undefined `_save_to_zip` from AgentExtend. Verify calls `write_changelog`/`generate_file_details` with no import. Five+ tools assume `_AGENT_WORK_DIR` is pre-defined in globals. | [03](components/03_maintenance.md), [02](components/02_generation.md) |

### 3. Doc ↔ code divergence

| Tier | Doc claims | Code reality | Drilldown |
|------|-------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------|
| 🟠 | `IndexWeighting.md`: indexes merge IndexHints vocabulary, weight P1–P5 | No IndexHints file ever read. No P1–P5 weighting code. L1 is a flat uniform list. | [01](components/01_indexing.md) |
| 🔴 | `GenerationRecipe.md` STEP 4: `verify_all(result["zip_path"])` | `AgentPackager.package_agent` returns `output_path` not `zip_path` → `KeyError` at STEP 5. | [02](components/02_generation.md) |
| 🔴 | Recipe: "use `import_alias`, do not guess module names" | 1/119 resources have `import_alias`. Bootstrap silently no-ops for the other 118. | [02](components/02_generation.md) |
| 🟠 | Recipe: "use API signatures from manifest, do not read source" | 0/119 resources have `api` field. The whole API-registry protocol is empty. | [02](components/02_generation.md) |
| 🔴 | MasterPrompt §10: pre_release_check is the release gate | `release_version` validates its own kwargs (Verify.py:1543-1563). Never consults the auto-tracker. DevProtocol step order is reversed from code. | [03](components/03_maintenance.md) |
| 🔴 | MasterPrompt §10: every file mutation requires `track_change` | Nothing in `release_version` checks for matching tracked changes. Can ship a release with zero `track_change` calls. | [03](components/03_maintenance.md) |
| 🟠 | MasterPrompt §4: menu loads via `load_menu` / `load_light_config` / `load_heavy_config` | All three are dumb static-file extractors. The 686 lines of dynamic menu code below them have no caller. | [04](components/04_menu_config.md) |
| 🔴 | `Core_Index_L1.md` STATS: 93 resources / 6546 KB | Manifest array has 119 resources. L1 was generated when there were 93 and never rebuilt. 26 added since → invisible to L0/L1 routing. | [05](components/05_manifest_drift.md) |
| 🟡 | Manifest claims to be the source of truth | Three writers, three schemas, four different counts inside the same JSON. | [05](components/05_manifest_drift.md) |
| 🟡 | `DualPlatform.md`: same `kb/` works on both Host-A and Copilot | Bootstrap does **not** use StorageAdapter — uses raw `zipfile.ZipFile`. Would error in Copilot folder mode. Theoretical: Copilot startup doesn't actually call Bootstrap (uses `read_file` per DualPlatform). | [02](components/02_generation.md) |

### 4. Context-window efficiency

| Where | Waste | Drilldown |
|--------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------|-----------|
| StartupLoader re-extracts and re-execs itself | Manifest lists `Core_Tool_StartupLoader.py` as `on_startup` — but the agent already exec'd it manually. Loop hits it; not marked loaded; re-extracts + re-execs. No bug, just wasted disk + time. | [02](components/02_generation.md) |
| AgentMenuBuilder dead code | 686 LOC of unreferenced `MENU_GROUPS`/`_REGISTRY`/`show_menu` machinery. When the model reads this module to find `load_menu`, it carries all 945 lines of context. | [04](components/04_menu_config.md) |
| TemplateGen dead code | Lists 10 nonexistent files (v3.x naming `agent_budget.py`, `agent_req.py`, etc.). Plus crashes on import. Pure deletion target. | [02](components/02_generation.md) |
| Verify size | 1940 LOC / ~80 KB. ~20K tokens of "one tool" — the model can't load it without dominating context. | [03](components/03_maintenance.md) |
| AgentPackager output noise | Prints "Phase 5: ZIP written" twice, dumps full README to stdout, ~50 lines of phase chatter on every generation. All goes to context. | [02](components/02_generation.md) |
| `Common_Tool_PptxTemplate.pptx` is 4.5 MB in the ZIP | Listed in manifest as `on_demand` correctly, but the agent has to filter it every time it scans resources. Worth folder-sharding. | [05](components/05_manifest_drift.md) |
| Verbose phase prints in `verify_all` and `run_regression` | Every test result prints ~3 lines. With T1 having 20 checks + T2 + T3 + T4 → ~100 lines into context per release. | [03](components/03_maintenance.md) |

---

## Proposed rewrite shape

Bigger picture coming out of all four audits — written so you can sanity-check the direction before any code is touched.

### A. One pipeline, one orchestrator

**Today** four concentric onion layers do overlapping work:
- The recipe (markdown, calls Python inline)
- `Core_Tool_Bootstrap.py`
- `Core_Tool_StartupLoader.py`
- `Core_Tool_AgentGen.py` (orphan), `IdentityGen`, `AgentPackager`

**Proposed:**
```
agent/
 bootstrap.py — open agent (ZIP or folder) → return (manifest, file_dict)
 routing.py — manifest → L0/L1 indexer (single source for all keyword/entity logic)
 packager.py — assemble new agent from inputs (replaces AgentGen + IdentityGen + AgentPackager)
 verify.py — read-only checker (no release pipeline mixed in)
 release.py — release pipeline (extracted from current Verify)
 changelog.py — changelog + version-merge (auto-tracker backed by disk JSON, not globals)
 menu.py — single dict-driven menu (one source for text + JSX configurators)
 schema.py — naming/area/type/load-mode validators (single regex)
```

Six concrete deletes:
- `Core_Tool_AgentGen.py` (orphan)
- `Core_Tool_TemplateGen.py` (broken import, lists nonexistent files)
- 686 LOC of dead dynamic-menu code in `AgentMenuBuilder.py`
- The duplicate `_classify_resource`/`_DOMAIN_KEYWORDS`/`_build_l0`/`_build_l1` in `AgentPackager.py` (~600 LOC)
- The v1 manifest support in `AgentExtend.py` (`knowledge_sources`/`custom_modules`)
- `Core_Tool_DomainStub.py` from the factory's own KB (it's a template for *generated* agents)

### B. Make the manifest the actual source of truth

- **One schema version**, enforced at every read. Old v1 readers refuse to open new manifests; new readers refuse v1 entirely (one-shot migrator).
- **Single writer**: `KBManifest.add_resource` becomes the only path. AgentExtend, AgentPackager, RebuildIndexes all delegate.
- **One vocabulary for load semantics**: collapse `extract_to_disk` / `disk_exec` / `extract_and_exec` → `extract`. Collapse `read_to_variable` / `python_var` → `variable`. Collapse `read_to_context` / `context` → `context`. Document the 3-way enum in `schema.py`.
- **One area naming**: keep the slashed form (`tech/salesforce`, `business/energy`). Delete the `tech` / `Bus_ENRG` shorthands from resource entries.
- **`import_alias` and `api` either become mandatory and enforced at release, or get removed**. Today they're contract on paper, dead on disk.
- **One version field**, one updated timestamp.
- **`stats` becomes a computed view, not a stored copy** — generate on read, never persist. Eliminates 119/93/98/92 drift.

### C. Real indexer

Per Audit #1 recommendations:
1. **Make `IndexHints/*.md` a first-class input.** RebuildIndexes reads them, merges vocabulary + entity catalog + per-file content scan (full content, TF-IDF, not just docstring), emits per-domain *distinguishing* keyword lists.
2. **Stable domain IDs from content hash or alphabetical key.** No more renumbering across rebuilds.
3. **Two-pass weighted L1.** Pass 1: classify weight (P1–P5) from area + identity. Pass 2: emit hierarchical sub-sections for P1–P2, terse one-liners for P4–P5. Restores D11-style structure as a tool feature.
4. **Incremental rebuild.** Compare `content_hash` against prior manifest; only re-analyse changed files.
5. **One classifier**, not two (delete the AgentPackager copy).

### D. Real changelog enforcement

Per Audit #3:
- Move auto-tracker state to `kb/dev/_session_state.json`. Every `track_change` / `set_session_author` flushes immediately. Survives sandbox restart.
- Make `release_version` import and consult `pre_release_check` for real — or remove the auto-tracker entirely. Currently neither works.
- One changelog filename: `kb/dev/Dev_KB_Changelog.json`. Delete the loose `agent_changelog.json` writer in Verify.

### E. De-couple from the shared globals dict — **rewrite prerequisite, not a runtime bug**

Today the framework runs because StartupLoader.py:344 `exec(open(...).read, globals)` every tool into one shared namespace, where `_AGENT_WORK_DIR`, `_AGENT_ROOT`, `monkey_patch_zipfile`, and every other loaded tool's symbols are visible to every subsequent tool. This is **intentional** per `Core_Instr_DualPlatform.md` and is the only reason `Core_Tool_Config.py:20`, `AgentMenuBuilder.py:789`, `Repair.py:158/166`, `Verify.py:27`, `Report.py`, `AgentExtend.py`, and `TemplateGen.py:34` don't crash.

This works for the agent. It blocks the rewrite:
- No tool is testable outside the conversational sandbox.
- Splitting into normal Python modules requires rewiring every cross-reference (`ChangelogWriter.merge_versions` → `_save_to_zip` from AgentExtend's globals; Verify → `write_changelog`/`generate_file_details` from ChangelogWriter's globals; etc.).
- New developers cannot reason about a single tool in isolation.

Proposed during the rewrite — replace every module-scope reference like this:
```python
# Before — works in exec runtime, NameError on import
_WORK_DIR = os.getenv("AGENT_WORK_DIR", _AGENT_WORK_DIR)
```
with this:
```python
# After — works in both contexts, resolves at call time
def _work_dir -> str:
 return os.environ.get("AGENT_WORK_DIR", "/mnt/data")
```

Once done, normal `import` works. Pytest works. Tools can move into a proper `agent/` package.

### F. Trim runtime chatter

- `print` everywhere → structured logger with a `verbose` flag the model can lower.
- Verify writes its phase-by-phase report to disk (`kb/dev/_last_verify.json`), prints a one-line summary to the conversation. The model can `cat` the file if it wants details.
- AgentPackager same treatment — one summary line, full report on disk.

---

## Suggested rewrite roadmap (optional)

| Phase | Scope | Risk | Outcome |
|-------|---------------------------------------------------------------------------------------------------------------|------|--------------------------------------------------------------------------------------------------------|
| 0 | **Deletes only.** Remove AgentGen, TemplateGen, dead menu code, DomainStub, v1 AgentExtend paths. | Low | -2000 LOC. No behaviour change (was all dead). |
| 1 | **One manifest schema.** Single writer, single vocab, `stats` becomes computed view, `import_alias` required. | Med | Eliminates 4-way count drift. Bootstrap becomes 30 LOC. Forces a one-shot migration of the current ZIP. |
| 2 | **Real indexer.** Per Audit #1: hints input, weighted L1, stable IDs, incremental rebuild. | Med | Fixes "poor mapping/indexing". Q&A routing actually works for non-SF projects. |
| 3 | **Decouple globals.** Explicit imports, `WORK_DIR` from env, kill the shared-namespace assumption. | Med | Code becomes testable outside the agent runtime. Enables phase 4. |
| 4 | **Split Verify, real changelog persistence, single menu source.** | Med | Verify becomes 4 modules <500 LOC each. Changelog enforcement actually works. Menu drift impossible. |
| 5 | **Streaming/incremental I/O, structured logging, on-disk reports.** | Low | Eliminates context bloat from `print` chatter. Faster perceived response time. |

Phase 0 alone is a big win — it removes ~30% of the core engine without changing any working behaviour.

---

For specific line numbers, code excerpts, and detailed evidence per component, see the five drilldowns under `components/`.
