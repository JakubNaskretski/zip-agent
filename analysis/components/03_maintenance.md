# Component Drilldown — Maintenance Tooling

**Files audited:**
- `kb/core/Agent Extension/Core_Tool_Verify.py` (1940 LOC) — **god-class**
- `kb/core/Agent Extension/Core_Tool_ChangelogWriter.py` (1389 LOC)
- `kb/core/Agent Extension/Core_Tool_KBManifest.py` (608 LOC)
- `kb/core/Agent Extension/Core_Tool_KBSchema.py` (304 LOC)
- `kb/core/Agent Extension/Core_Tool_AgentExtend.py` (623 LOC)
- `kb/core/Agent Extension/Core_Tool_Repair.py` (403 LOC)
- `kb/core/Agent Extension/Core_Tool_Report.py` (353 LOC)

**Verdict:** the maintenance layer is where the framework leaks the most. **🔴 `release_version` bypasses `pre_release_check` entirely** — the changelog enforcement protocol is broken at the actual release entry point. **🟡 Auto-tracker uses module globals** — works within a single Python session, blocks the rewrite, fragile across sandbox restarts. Verify is a god-class with 4 concerns wedged together. 3 writers to the manifest with 3 different schemas. Hidden global coupling that **works by design** in the agent runtime but blocks any split into proper modules.

**Severity legend** (see [`04_VERIFICATION.md`](../04_VERIFICATION.md) for full definitions):
🔴 = real runtime failure · 🟠 = works but produces bad UX · 🟡 = works in the agent runtime by design (shared-globals `exec`), blocks the rewrite.

---

## 1. Per-tool one-paragraph

| Tool | What it is |
|-------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **KBSchema** (304 LOC) | Static vocabulary + `ResourceDescriptor` dataclass. `AREAS`, `RESOURCE_TYPES`, `LOAD_MODES = {on_startup, on_demand}` (KBSchema.py:31-34), filename parse/validate, `LEGACY_TO_NEW` migration. **Pure library — no I/O, no state.** Should be the schema authority. |
| **KBManifest** (608 LOC) | CRUD layer over `agent_manifest.json` v2: `create/add/remove/update/query` resources, index staleness tracking, v1→v2 migrator (`_map_v1_strategy` knows only `auto/on_demand/on_startup`), `audit_manifest` delegates to KBSchema. |
| **Verify** (1940 LOC) | God-class. 7 sections wedged together: test framework + zip/AST helpers + T1 integrity + T2 tool health + T3 instruction sync + release pipeline + next-session generator + bolted-on T4 consistency. |
| **ChangelogWriter** (1389 LOC) | Three concerns glued: (a) diff/details builder; (b) **fake** in-session auto-tracker using module globals; (c) Version Merge Protocol (compare/report/merge). |
| **AgentExtend** (623 LOC) | **v1-schema ZIP mutator.** Reads a `agent_manifest.json` shape with `knowledge_sources`/`custom_modules`/`load_strategy=auto` — which is NOT the v2 shape KBManifest writes. |
| **Repair** (403 LOC) | Generic try/retry wrapper with category dispatch tables. Module-level mutable `_config`/`_stats`. Soft-imports `agent_tracker.log_action`. |
| **Report** (353 LOC) | Pattern detector over tracker+repair stats. 8 hardcoded heuristics. Soft-imports `agent_tracker`/`agent_repair`. Crashes if a box-drawing char appears (Report.py:304-306). |

## 2. Auto-tracker — two findings of different severity

```python
# ChangelogWriter.py:423-425
_pending_changes: list = []
_session_author: Optional[str] = None
_session_rationale: Optional[str] = None
```

These are **Python module globals**. Two separate concerns to disentangle:

### 2a. 🔴 `release_version` bypasses `pre_release_check` (real bug)

Verify.py:1543-1563 validates `author`, `rationale`, `changes_list` as **parameters** passed directly to `release_version`, completely bypassing the auto-tracker state. The DevProtocol step 9→10→11 (flush → check → release) is reversed from how the code actually works: `release_version` accepts changes as kwargs and runs `pre_release_checklist` (Verify.py:1456) which calls `run_regression(run_exec=False)` — never reads `_pending_changes`/`_session_author`/`_session_rationale`. The auto-tracker is on a separate code path that only fires if you call `flush_to_changelog` first.

**Impact today:** MasterPrompt §10 ("Changelog Auto-Tracking — NON-NEGOTIABLE") is unenforceable. An agent that calls `release_version(author="x", rationale="y", changes_list=[{...}])` directly will succeed even if it has called `track_change` zero times — and even if those tracked changes contradict the changes_list. This is a real runtime issue.

### 2b. 🟡 Module-global state model (design fragility)

| Scenario | What happens |
|-----------------------------------------------------------|-------------------------------------------------------------------------------------------------------|
| Multi-turn within one Python session | **Works correctly** — module globals persist across model turns inside the same code-interpreter process. The agent can `track_change` on turn 1, `track_change` again on turn 2, `flush_to_changelog` on turn 3. |
| Sandbox restart between turns | State evaporates. `_pending_changes == []`, author/rationale `None`. Next `pre_release_check` blocks with "no changes tracked". |
| Mid-session crash / timeout / re-exec of the module | State lost silently. |
| Multiple agents being audited in parallel | All share the same module globals — impossible. |
| Recovery path | None. No on-disk shadow file, no `kb/dev/pending.json`, nothing in manifest. |

**Impact today:** survivable within a typical Host-A session that stays alive. Not the cause of any observed bug. **Impact on rewrite:** the rewrite cannot rely on module globals — the auto-tracker has to persist to disk (e.g. `kb/dev/_session_state.json`) on every mutation. Otherwise it can't be tested outside the runtime and it can't be moved into a proper package.

## 3. Three writers to the manifest, three schemas

The `agent_manifest.json` on disk shows resource entries with `load_strategy: extract_to_disk` / `read_to_variable` / `read_to_context` / `extract_and_exec` / `disk_exec` / `python_var` / `context`. **None of these strings exist as valid values in any of the three known writers' code.**

| Writer | Shape it writes | Vocabulary |
|---------------|------------------------------------------------------------------------------|---------------------------------------------------------|
| KBManifest | v2: `resources[]`, `load_mode`, no `load_strategy` | `load_mode ∈ {on_startup, on_demand}` (from KBSchema) |
| AgentExtend | v1: `knowledge_sources[]`, `custom_modules[]`, top-level `load_strategy=auto`| `load_strategy ∈ {auto, on_demand, on_startup}` |
| Mystery | v2: `resources[]`, `load_mode` + extra `load_strategy=extract_to_disk/...` | At least 4 unique strings none of the above know about |

The "mystery writer" is likely **`Core_Tool_RebuildIndexes.py:309-326`** (per Audit #1) which chooses load_strategy from a different table. So we have **at least three** schemas living in the same file. KBSchema.LEGACY_TO_NEW (KBSchema.py:173-206) suggests a migration was planned but never completed.

## 4. Verify is a god-class

Sections wedged into one 1940-LOC file:

| Section | Lines | Concern |
|---------|-------------|---------------------------------------------------------------|
| §1 | 63-201 | Test framework dataclasses (TestResult, TestSuite, VerifyReport) |
| §2 | 210-387 | Zip/AST helpers (_load_manifest_from_zip, _extract_cross_references, etc.) |
| §3 | 397-671 | T1 integrity: 20 checks, ~270 LOC |
| §4 | 681-898 | T2 tool health: AST parse, `exec` sandbox at :815, JSX heuristics |
| §5 | 908-1165 | T3 instruction sync |
| §6 | 1175-1283 | Orchestrator + API baseline + history (last-50 rotation) |
| §7 | 1386-1725 | **Release pipeline** (detect_changes, pre_release_checklist, build_zip, release_version, verify_all) — completely different concern, just shares the regression call |
| §7b | 1739-1767 | `agent_next_session` — wraps a different module entirely. Doesn't belong here. |
| §8 | 1779-1940 | **Bolted-on T4 consistency** (CON-001…CON-010). Even has redundant `import zipfile, json, re` inside the function body at :1795. |

Performance issues:
- **AST parsed 4× per regression**: `_extract_public_functions`/`_extract_class_methods` reimplemented inside `verify_tool_health` (Verify.py:776-777), `verify_instruction_sync` (:955-957), `generate_api_baseline` (:1205-1207); T2 and T3 each rebuild `tool_api_registry` separately (:947-963 ≈ duplicates :776).
- **T2.04 execs every Python tool** in a sandbox (Verify.py:815) — slow, non-deterministic, false WARNINGs.
- **`_extract_cross_references`** (:268-290) reads every text file in the ZIP into memory.
- **`run_regression` defaults `run_exec=True`** (:1287) — `verify_all` and `pre_release_checklist` both pass `False`, but other call sites use the default.

## 5. AgentExtend — domain leak into core

```python
# AgentExtend.py:434-443 (inside suggest_reusable)
SF_PATTERNS = ['sf_kb_model', 'sf_kb_relationship', 'sf_validation', ...]
```

Hardcoded Salesforce patterns in a tool that claims to be domain-agnostic ("Custom Agent Modules"). This is the same anti-pattern as the SF-flavored `TOPIC_KEYWORDS` in RebuildIndexes — domain knowledge bleeding into framework code.

Also:
- Writes a v1 manifest shape — incompatible with KBManifest's v2 shape.
- AgentExtend.py:99-102: `mod.get("load_strategy") == "on_demand"` checked twice in adjacent branches. Dead branch.
- `_default_manifest` (AgentExtend.py:31) **silently fabricates a fresh v1 manifest** if loading fails. Potential silent data loss.
- `_save_to_zip` (AgentExtend.py:57-73) is called from ChangelogWriter's `merge_versions` (cl:1379) with **no import** — works only because both files are exec'd into the same globals dict.

## 6. Hidden global namespace coupling — 🟡 design fragility (works today, blocks rewrite)

| Site | Calls | Defined in | Import? |
|-----------------------------------------------|--------------------------------|------------------------------|----------|
| ChangelogWriter.py:1379 | `_save_to_zip(...)` | AgentExtend.py:57 | No |
| Verify.py:1579, :1593 | `write_changelog`, `generate_file_details` | ChangelogWriter.py:412 | No (docstring at :23-25 says "Requires Core_Tool_ChangelogWriter.py to be loaded (exec'd) before release") |
| Verify.py:27 | `_AGENT_WORK_DIR` | StorageAdapter.py:44 | Implicit via globals |
| Repair.py:158, :166 | `_AGENT_WORK_DIR` | StorageAdapter.py:44 | Implicit via globals |
| Report.py (multiple) | `agent_tracker.get_log`, `agent_repair.get_repair_stats` | Other modules | `try: import / except: pass` (soft) |

**This works in the agent runtime by design.** StartupLoader.py:344 `exec`s every Tool into `globals`, so `_save_to_zip`, `write_changelog`, `_AGENT_WORK_DIR`, etc. are all visible to every subsequently-loaded tool. The `Requires ... to be loaded (exec'd) before release` comment in Verify.py:23-25 is a deliberate dependency-on-runtime-order, not a missing import.

**It is also the single biggest blocker for the rewrite.** Nothing can be `import`ed in a normal Python context. Nothing can be unit-tested in isolation. Splitting Verify into 4 files requires rewiring every cross-reference. Moving to a `agent/` package means rewriting every "implicit via globals" line as an explicit `from <module> import <symbol>`.

## 7. Three places implement naming-convention regex

```python
# KBSchema.py:154-171 — `validate_filename`
# Verify.py:33-38 — `_NAMING_PATTERN`
# Verify.py:1916 (inside CON-008) — `re.findall(r'"prefix":\s*"(\w+)"', schema_content)`
```

Verify CON-008 (Verify.py:1907-1924) **doesn't even import KBSchema** — it re-parses KBSchema's source code as text to extract the valid prefixes. Three parallel implementations, kept in sync by hand.

## 8. Other smells

- **Filename divergence**: Verify writes the loose copy at `_CHANGELOG_FILE = "agent_changelog.json"` (Verify.py:28) while inside the ZIP the file is `Dev_KB_Changelog.json` (ChangelogWriter.py:36). Same data, two names.
- **Version Merge writes to two paths**: ChangelogWriter.py:1364-1365 writes both `kb/dev/Dev_KB_Changelog.json` and root `Dev_KB_Changelog.json`. Compare reader (`_compare_changelogs` :1029-1044) tries `kb/dev/...` first then root. Inconsistent read/write paths.
- **`agent_next_session`** (Verify.py:1739-1767) wraps `Common_Tool_Session.py` from inside Verify. Doesn't belong in the regression tester.
- **Verify history capped at 50** (Verify.py:1277-1278) but rewrites the entire history on every save. O(N) write per regression.
- **Repair retry sleeps inline** (`time.sleep(_config["retry_delay"])` at Repair.py:184). 3 retries × 0.5s = 1.5s blocking.
- **Report.py:304-306** — `assert` raises if any Unicode box-drawing char slips in. A bad input can crash the entire report generator.
- **AgentPackager copies factory's changelog details files** unless filtered. The auto-tracker has a filter (mentioned in earlier audit) — but the divergence between `agent_changelog.json` and `Dev_KB_Changelog.json` means new agents can ship with stale Factory history.

## 9. Top 8 file-line items for the rewrite

| # | Action | Files |
|---|---------------------------------------------------------------------------------------------------------------------|-----------------------------------|
| 1 | **Persist auto-tracker state to disk.** Replace `_pending_changes`/`_session_author`/`_session_rationale` globals with `kb/dev/_session_state.json`. Flush on every mutation. | ChangelogWriter.py:423-489 |
| 2 | **Make `release_version` actually call `pre_release_check`.** Today it bypasses it. Either wire it as the real gate, or remove the auto-tracker entirely. | Verify.py:1514-1678, ChangelogWriter.py:492-522 |
| 3 | **Split Verify into 4 files.** `verify_framework.py` / `verify_integrity.py` / `verify_tools.py` / `verify_instructions.py`. Move release pipeline to `release.py`. Move `agent_next_session` out. | Verify.py (all) |
| 4 | **Memoize AST parses.** One `tool_api_registry` per regression, shared by T2/T3/baseline. | Verify.py:776, :955, :1205 |
| 5 | **Delete v1 AgentExtend code paths.** One-shot migrator, then refuse v1. Make KBManifest the single writer. | AgentExtend.py:23-54, :110-220 |
| 6 | **One naming regex.** Verify CON-008 and T1.11 call `KBSchema.validate_filename` instead of re-implementing. | KBSchema.py:154-171, Verify.py:33-38, :1907-1924 |
| 7 | **Explicit imports.** Drop the "exec into shared globals" assumption — proper `from .changelog_writer import ...` everywhere. | All seven files |
| 8 | **Remove T2.04 `exec` test or feature-flag it.** Slowest, most fragile test in regression. Or sandbox-only mode. | Verify.py:815 |

## 10. Spec ↔ code divergence summary

| Doc claims | Code reality |
|-------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------|
| MasterPrompt §10: every file mutation calls `track_change` | Nothing enforces it; release path doesn't consult tracker |
| MasterPrompt §10: `pre_release_check.ok` blocks release | Only blocks `flush_to_changelog`. `release_version` validates own kwargs and bypasses |
| DevProtocol step 9→10→11 (flush → check → release) | Reversed in code: `release_version` runs first, calls `pre_release_checklist` (different from `pre_release_check`) |
| L1+L2 written every release | True for `flush_to_changelog` path; not for direct `release_version` kwargs path |
| Version Merge writes a merge entry (cl:1187) | True, but to two different paths read inconsistently |
| Manifest is the source of truth | 3 writers, 3 schemas, 4 different self-counts |
| Verify gives consistency checks | T4 (CON-001…CON-010) is bolted on; re-parses KBSchema source as text instead of importing |
| `agent_next_session` is a session-handoff tool | Lives inside Verify (Verify.py:1739) — wrong file |
