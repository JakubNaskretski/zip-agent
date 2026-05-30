# Component Drilldown — Generation Pipeline

**Files audited:**
- `kb/core/Core_Tool_StartupLoader.py` (495 LOC)
- `kb/core/Core_Tool_StorageAdapter.py` (396 LOC)
- `kb/core/AgentFactory/Core_Tool_Bootstrap.py` (195 LOC)
- `kb/core/AgentFactory/Core_Tool_AgentGen.py` (912 LOC) — **orphan dead code**
- `kb/core/AgentFactory/Core_Tool_IdentityGen.py` (732 LOC)
- `kb/core/AgentFactory/Core_Tool_AgentPackager.py` (973 LOC)
- `kb/core/AgentFactory/Core_Tool_TemplateGen.py` (253 LOC) — **broken on import, dead code**
- `kb/core/AgentFactory/Core_Instr_GenerationRecipe.md` — spec

**Verdict:** four parallel re-implementations of "open agent definition → extract Python tools", plus an orphan secondary generator (`AgentGen`) and a legacy template tool (`TemplateGen`) referencing pre-v1.0 filenames. The recipe lies about `import_alias` / `api` and returns the wrong dict key (`zip_path`) that the recipe then tries to read — that one will actually `KeyError` at runtime.

**Severity legend** (see [`04_VERIFICATION.md`](../04_VERIFICATION.md) for full definitions):
🔴 = real runtime failure · 🟠 = works but produces bad UX · 🟡 = works in the agent runtime by design (shared-globals `exec`), blocks the rewrite.

---

## 1. Call graph reality

```
RECIPE PATH ORPHAN PATH (AgentGen.py)
───────────── ───────────
ask user platform AgentGen.generate_agent_def(...)
↓ ├─ imports sf_agent_gen.generate_agent
inline bootstrap ├─ imports agent_template_gen.generate_agent_template
(GenerationRecipe.md:34-58) │ └─ TemplateGen has NameError on import → crashes here
↓ └─ _merge_outputs_v2 — produces a parallel "agent_prompt.md"
sf_pull.pull_core(metadata.zip) agent structure that the recipe doesn't recognise
↓
sf_agent_gen.generate_agent(...)
↓
agent_identity_gen.generate_identity(...) ← pure templating
↓
agent_packager.package_agent(...)
 └─ 5-phase: copy_factory → copy_kb → overlay_identity → reclassify → write
↓
agent_verify.verify_all(result["zip_path"]) ← KeyError, real key is "output_path"
```

Three separate "extract Python tools" implementations:
1. Inline in GenerationRecipe.md:34-58 (recipe path)
2. `Core_Tool_Bootstrap.py` (importable, uses `importlib`)
3. `Core_Tool_StartupLoader.py` (exec-based, uses `_AGENT_WORK_DIR`)
4. `AgentPackager.package_agent` Phase 1 (full ZIP copy)

## 2. AgentGen — orphan code, 🟡 design fragility

`Core_Tool_AgentGen.py` (912 LOC) is the **only file claiming to be the "Unified Generator"** (AgentGen.py:1 docstring), but the recipe doesn't call it.
- Imports `sf_agent_gen` at AgentGen.py:286
- Imports `agent_template_gen` at AgentGen.py:311 (see §3 below — TemplateGen has shared-globals dependencies that mean it can `import` cleanly only if loaded via the framework's exec-into-globals path, not via normal `importlib.import_module`)
- The recipe's path (Bootstrap → IdentityGen → AgentPackager) bypasses AgentGen entirely

Also, AgentGen.py:228 references `config` which is not in scope — the parameter is `knowledge_config`/`common_config`. Would `NameError` on the `output_path is None` branch *if anything reached it* — but nothing does.

**Status: orphan code.** Not a runtime bug because no caller. Pure deletion target — duplicates the recipe's job with a different output shape and stale naming.

## 3. TemplateGen — 🟡 design fragility, lists nonexistent files

```python
# TemplateGen.py:21-32
_TEMPLATE_FILES = [
 "agent_budget.py", "agent_req.py", "agent_verify.py", "agent_config.py",
 "agent_help.py", "agent_menu.py", "agent_modes.py", "agent_domain.py",
 "agent_md_format.md", "shared_session.py",
]
_WORK_DIR = os.getenv("AGENT_WORK_DIR", _AGENT_WORK_DIR)
```

Two concerns of different severity:

- **🟡 Design fragility (not a runtime bug today):** line 34 references `_AGENT_WORK_DIR` at module scope. On a normal `importlib.import_module("agent_template_gen")` this would `NameError`. In the agent runtime, the framework would `exec` the file into globals where `_AGENT_WORK_DIR` already exists from StorageAdapter — so it loads fine. But since nothing actually calls TemplateGen in the recipe path, neither outcome matters today.
- **🟡 Pre-v1.0 filename list:** every entry is the v3.x naming (`agent_budget.py`, etc.). The v1.0+ KB uses `Common_Tool_Budget.py`, `Common_Tool_Requirements.py`, etc. Even if the module loads, every `os.path.exists(fpath)` at TemplateGen.py:90 returns False → generated ZIP is empty. Pure consolidation residue.

**Status: dead code.** Two issues that cancel each other — the file is broken in two ways but nothing reaches it. Delete.

## 4. Bootstrap ↔ StartupLoader — duplicate work, different mechanisms

| Aspect | `Core_Tool_Bootstrap.py` | `Core_Tool_StartupLoader.py` |
|---------------------------------|---------------------------------------------------|--------------------------------------------------|
| Selection criterion | Iterates ALL `.py` resources | Only `load_mode == "on_startup"` (10 entries) |
| Loading mechanism | `importlib.import_module(alias)` (Bootstrap.py:139) | `exec(open(...).read, globals)` (StartupLoader.py:344) |
| Depends on `import_alias` | Yes — silently skips if missing (Bootstrap.py:111, :135) | No |
| Work dir | `tempfile.mkdtemp("agent_rt_")` (Bootstrap.py:60) | `_AGENT_WORK_DIR` (default `/mnt/data`) |
| Uses StorageAdapter | **No** — raw `zipfile.ZipFile` | Yes (bootstraps it first) |
| Result format | `rt` dict with `modules/manifest/work_dir/...` | `StartupResult` dataclass |
| Caller in the recipe | Recipe STEP 1 — but the inline code on those lines is a third *re-implementation*, not a call to Bootstrap |

Bootstrap is named "the canonical bootstrap" by its docstring but **the recipe doesn't actually use it** — STEP 1 is an inlined re-implementation (GenerationRecipe.md:34-58). Bootstrap exists, is loaded into the agent's tools, and just sits there. **Status: 🟡 design fragility** — orphan in the recipe path.

**Bootstrap doesn't go through StorageAdapter** — uses raw `zipfile.ZipFile`. `zipfile.ZipFile(folder_path)` would raise in Copilot folder mode. **Status: 🟡 theoretical** — Copilot startup doesn't actually invoke Bootstrap (per `Core_Instr_DualPlatform.md`, Copilot reads files via `read_file`, not Python bootstrap), so the broken-Copilot scenario never executes today. If the runtime later starts to use Bootstrap on Copilot, this becomes 🔴.

## 5. The `import_alias` / `api` lie

GenerationRecipe.md says:
> 3. **Use `import_alias` from manifest** — do not guess module names
> 4. **Use API signatures from manifest** — do not read tool source code

Reality (from manifest inspection earlier):
```
import_alias: 1 of 119 resources have it set
api: 0 of 119 resources have it set
```

Cascade (severity 🔴 — these actually fire at runtime):
- Bootstrap.py:111 silently skips alias creation for 118 of 119 tools
- Bootstrap.py:135 `not alias: continue` skips the entire `importlib.import_module` for 118 of 119 tools — Bootstrap effectively imports zero modules out of the box
- AgentPackager.py:782-787 enrichment block always receives `None` for `import_alias_map` and `api_registry`
- The recipe tells the agent to use `agent_identity_gen` and `agent_packager` (the aliases) but those don't exist in the manifest — the agent has to fall back to raw filenames or fail

The "use manifest, don't read source" protocol is contract on paper, broken on disk. The agent runtime works because the inline recipe step (and Bootstrap path) silently degrades to filename-based loading — but the *recipe's stated semantics* never run.

## 6. IdentityGen — pure templating, no identity

Despite the name, `generate_identity` does **not** read user knowledge, **does not** call RebuildIndexes, **does not** customize the prompt from project content. It's a giant f-string template (IdentityGen.py:234-547) with `if has_sf:` branches and a hand-coded SF tool list (IdentityGen.py:421-457).

Issues:
- L0 output is a placeholder string (IdentityGen.py:638-643): `"Placeholder — will be rebuilt by AgentPackager Phase 4.5"`. Real L0 is regenerated downstream.
- The recipe (STEP 3) shows `generate_identity(agent_name, version, config, sf_stats, factory_version)` — but the real signature also takes `resource_inventory` (IdentityGen.py:654). The recipe omits it. Without it, L1 is the placeholder.
- Even when `resource_inventory` is passed, the resulting L1 is a flat alphabetic list (IdentityGen.py:672-677) — no domain grouping, no weighted sub-sections. Same flaw as RebuildIndexes.

**Identity-driven customization (the whole premise of `Core_Instr_IndexWeighting.md`) is not implemented anywhere in IdentityGen.**

Minor: IdentityGen.py:695 — `hasattr(datetime.now, 'isoformat')` is always True. Dead conditional.

## 7. AgentPackager — works but bloated and noisy

Phases:
1. **Phase 1** (AgentPackager.py:691) — full read of Factory ZIP, copy everything except `FACTORY_ONLY_FILES`. **No per-preset filtering** — G1 lite still ships all of `kb/tech/salesforce/*`.
2. **Phase 2** (AgentPackager.py:712) — pull 11 hardcoded files from `project_kb_zip` (`SF_KB_FILE_MAP`).
3. **Phase 3** (AgentPackager.py:730) — overlay `identity_files` via `IDENTITY_PATH_MAP`.
4. **Phase 4** (AgentPackager.py:757) — re-walk every file, MD5 + regex docstring per file, rebuild manifest.
5. **Phase 4.5** (AgentPackager.py:795) — regenerate L0/L1 from in-memory inventory. Uses its own duplicated copy of `_classify_resource`/`_DOMAIN_KEYWORDS`/`_build_l0`/`_build_l1` (AgentPackager.py:255-549).
6. **Phase 5** (AgentPackager.py:932 + :944) — print "ZIP written" twice (line printed twice in code), dump full README to stdout.

Bugs and divergence:
- **AgentPackager.py:957 returns `output_path`, not `zip_path`.** Recipe STEP 5 does `verify_all(result["zip_path"])` → `KeyError`.
- **`FACTORY_ONLY_FILES` differs** between AgentPackager.py:50-72 (23 files) and IdentityGen.py:54-85 (28 files). The 5-file difference means `Core_Tool_IdentityGen.py`, `Core_Tool_AgentPackager.py`, `Core_Tool_Bootstrap.py`, `Core_Instr_GenerationRecipe.md`, `Core_Tool_AgentGen.py` get copied into every generated agent's `kb/core/AgentFactory/` (because AgentPackager's exclusion list doesn't have them).
- **Duplicated classifier**: ~600 LOC of `_classify_resource`/`_discover_domains`/`_build_l0`/`_build_l1`/`_DOMAIN_KEYWORDS`/`_DOMAIN_CLASSIFIERS` are duplicated between AgentPackager.py:255-549 and RebuildIndexes.py:401-1017. Kept loosely in sync by hand.
- **AgentPackager.py:209-212** — same `if p["common_modules"] is not None:` checked twice. Dead branch.
- **`_generate_copilot_files`** (AgentPackager.py:929) runs whenever `output_format=="folder"` OR `target_platform=="copilot"` — so Host-A users who pass `output_format="folder"` get Copilot artifacts written. Surprising.

Performance per generation:
- 2 full reads of Factory ZIP (Bootstrap + Phase 1)
- MD5 + regex docstring on every file (~120 files), sequential, no caching
- L0/L1 generation iterates `resources` 4 more times
- Verbose `print` chatter goes into the conversation context

## 8. StartupLoader — works but does redundant work

Behaviour is mostly correct (loads StorageAdapter first, monkey-patches `zipfile`, then routes resources by type). Two minor wastes:
- **Re-extracts and re-execs itself.** `Core_Tool_StartupLoader.py` is listed as `on_startup` in the manifest. On first run, the loop hits its own filename, finds `is_loaded(...)` is False (the agent exec'd it manually, but didn't call `mark_loaded`), extracts it again, exec's it again. Re-defines functions. No bug — just wasted disk write + re-exec.
- **`Core_Tool_StorageAdapter.py` has `load_strategy: None`** in the manifest. StartupLoader bootstraps it before the main loop via `_bootstrap_storage_adapter` (line 179) and marks it loaded (line 223). Main loop then sees `is_loaded("Core_Tool_StorageAdapter.py")` and skips. Correct, just unusually ordered.
- **`Core_Instr_DualPlatform.md`** has `load_strategy: None`; `Core_Instr_DevProtocol.md` has `load_strategy: read_to_variable`. Different vocab for the same intent ("hold in Python var until needed").

## 9. StorageAdapter — solid, but design choice creates surprise

`FSBackend` + `monkey_patch_zipfile` works as advertised. `zipfile.ZipFile(folder)` transparently returns an `FSBackend` after patching.

One real issue: **Bootstrap.py does not call `monkey_patch_zipfile` before opening the ZIP.** If you're on Copilot (folder mode) and you run the recipe's STEP 1 inline code (or call `Bootstrap.bootstrap(folder_path)`), the raw `zipfile.ZipFile(folder_path)` raises.

`resolve_agent_path` (line 273) uses 5 fallback mechanisms (`_AGENT_ROOT`, legacy globals via `inspect.stack`, scan work_dir for `AgentDefinition*.zip`, scan for folders, scan current dir for `agent_manifest.json`, scan `agent/` subfolder). Robust but has odd quirks — `inspect.stack` walk with `len(caller_globals) > 200` safety limit (line 297) is a strange convergence criterion.

## 10. Top 8 file-line items for the rewrite

| # | Action | Files |
|---|---------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------|
| 1 | **Delete `Core_Tool_AgentGen.py`** entirely. | AgentGen.py |
| 2 | **Delete `Core_Tool_TemplateGen.py`** entirely. | TemplateGen.py |
| 3 | **Collapse Bootstrap + StartupLoader + recipe-inline into one `agent.bootstrap.load(source)`.** Use StorageAdapter unconditionally. Return one shape regardless of platform. | StartupLoader.py, Bootstrap.py, GenerationRecipe.md |
| 4 | **Merge IdentityGen into AgentPackager.** Identity is just templated strings + an L0/L1 build from real inventory. Eliminates the `resource_inventory` round-trip and the `FACTORY_ONLY_FILES` divergence. | IdentityGen.py, AgentPackager.py |
| 5 | **Move the L0/L1 builder into one module shared by AgentPackager and RebuildIndexes.** ~600 LOC of dup deletes. | AgentPackager.py:255-549, RebuildIndexes.py:401-1017 |
| 6 | **Per-preset filter** at Phase 1 — when `tech_domains` excludes Salesforce, don't copy `kb/tech/salesforce/*`. | AgentPackager.py:691-705 |
| 7 | **Fix the recipe**: signature for `generate_identity` includes `resource_inventory`; `package_agent` returns a dict with both `output_path` and `zip_path` (deprecate one). | GenerationRecipe.md |
| 8 | **Reduce print chatter.** Single summary line; full report to disk (`kb/dev/_last_pack.json`). | AgentPackager.py (everywhere `print(`) |

## 11. Spec ↔ code divergence summary

| Recipe / spec claim | Code reality |
|----------------------------------------------------------------------|--------------------------------------------------------------------|
| STEP 1 uses `import_alias` from manifest | Manifest has alias on 1/119; recipe inline code skips 118/119 |
| STEP 3 signature: `generate_identity(agent_name, version, config, sf_stats, factory_version)` | Real signature has additional `resource_inventory`; omitted → L1 is placeholder |
| STEP 4: `package_agent(..., import_alias_map=<from_manifest>, api_registry=<from_manifest>)` | Both always `None` in practice; enrichment is a no-op |
| STEP 5: `verify_all(result["zip_path"])` | `result["zip_path"]` doesn't exist; key is `output_path` → KeyError|
| DualPlatform.md: same `kb/` works on Host-A + Copilot | Bootstrap doesn't use StorageAdapter → fails in Copilot mode |
| Recipe never mentions AgentGen | AgentGen exists, is 912 LOC, claims to be "Unified Generator" |
| AgentGen → TemplateGen → broken | TemplateGen has import-time NameError → AgentGen crashes on import |
| Generated agent reflects preset config | All Factory files ship regardless of preset; only L0/L1 hide them |
