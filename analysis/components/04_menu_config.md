# Component Drilldown — Menu / Config Layer

**Files audited:**
- `kb/core/Menu & Config/Core_Tool_AgentMenuBuilder.py` (945 LOC) — **mostly dead code**
- `kb/core/Menu & Config/Core_Tool_Config.py` (63 LOC) — near-stub with import-time bug
- `kb/core/Menu & Config/Core_KB_ConfiguratorRegistry.json` (~10 KB)
- `kb/core/Menu & Config/Core_Tool_AgentConfigurator_light.jsx` (~8 KB)
- `kb/core/Menu & Config/Core_Tool_AgentConfigurator_heavy.jsx` (~32 KB)
- `kb/core/Core_INSTR_Menu.md` — the static menu the user actually sees

**Verdict:** the user-visible menu is **a static markdown file**. The 945-LOC "Agent Dynamic Menu System" has no caller. Three unrelated menu vocabularies coexist. The two JSX configurators have already drifted because `light.jsx` re-inlines the registry JSON instead of importing it.

**Severity legend** (see [`04_VERIFICATION.md`](../04_VERIFICATION.md) for full definitions):
🔴 = real runtime failure · 🟠 = works but produces bad UX · 🟡 = works in the agent runtime by design (shared-globals `exec`), blocks the rewrite.

---

## 1. What `load_menu` / `load_light_config` / `load_heavy_config` actually do

All three are **dumb file extractors**, not dynamic systems. From `Core_Tool_AgentMenuBuilder.py`:

- **`load_menu(zip_path=None)`** (lines 709-744) — opens the ZIP, reads `kb/core/Menu & Config/Core_INSTR_Menu.md` verbatim, returns the markdown string. **No parsing, no manifest lookup, no field reads.** The static markdown table (G1–G5 / SF1–SF5 / DEV / MERGE / VERIFY / BUDGET / V1) is the entire menu.

- **`load_light_config`** (lines 747-820) and **`load_heavy_config`** (lines 823-896) are near-identical 70-line clones. Each opens the ZIP, reads one `.jsx`, writes it to `_AGENT_WORK_DIR/<filename>`, prints three lines, returns a `disk_path/char_count/line_count/artifact_type/instruction` dict.

**`_AGENT_WORK_DIR` is referenced at lines 789, 865 but never defined in this module.** Same pattern as `Core_Tool_Config.py:20`. **🟡 Design fragility, not a runtime bug**: works fine inside the agent runtime because StartupLoader execs every tool into globals where `_AGENT_WORK_DIR` already exists (set by StorageAdapter at line 44). Crashes only on a normal `import` — which the agent never does. Affects rewrite, not today's behaviour.

## 2. 686 lines of dead code

Lines 31-686 of `AgentMenuBuilder.py` are an entire "Dynamic Menu System": `MENU_GROUPS`, `_REGISTRY`, `_BASE_OPS`, `register_operation`, `discover_from_manifest`, `show_menu`, `resolve_operation`, `register_knowledge_ops`, `print_menu_summary`, `show_fallback_menu`, `add_group`…

**None of this is called from anywhere.** MasterPrompt §4 documents only `load_menu` / `load_light_config` / `load_heavy_config` as the entry points. The dynamic system was built and never wired up.

When the model needs to read this module to find `load_menu`, it carries all 945 lines of context.

## 3. The menu is NOT coupled to manifest `areas[].enabled`

`load_menu` does not parse the manifest at all. SF1–SF5 codes remain in the printed menu even if `areas["tech/salesforce"]["enabled"] = false`. The unused `discover_from_manifest` (AgentMenuBuilder.py:123-172) reads `manifest["knowledge"]` and `manifest["custom_modules"]` — **not** `areas[].enabled` — and would populate `KNOWLEDGE`/`MODULES` menu groups, but no caller invokes it.

## 4. Three unrelated menu vocabularies

Coexisting with no mapping between them:

| Vocabulary | Source | Examples |
|-------------------------------------|------------------------------------------|------------------------------------------|
| User-facing text codes | `Core_INSTR_Menu.md` | G1–G5, SF1–SF5, ASK, DOC, DEV, MERGE, VERIFY, BUDGET, V1 |
| Dev-protocol operations | `AgentMenuBuilder.py:49-60` (`_BASE_OPS`) | Backlog, Add Req, Stats, Verify, Release, Budget, Help, Export MD, Mermaid, Next Session |
| Generated-agent capability bundles | `Core_KB_ConfiguratorRegistry.json` | technologies/salesforce, domains/energy, common/doc_gen, agentCore, presets |

The text-menu codes (G1, SF1, etc.) **have no programmatic counterpart anywhere**. Can't be cross-validated against the registry or `_BASE_OPS`.

## 5. `Core_KB_ConfiguratorRegistry.json` contents

Top-level keys:

| Key | Line | Purpose |
|-----------------|------|---------------------------------------------------------------------------|
| `technologies` | 2 | Currently only `salesforce` — `knowledge[]`, `tools[]`, `projectSources[]`|
| `domains` | 120 | `energy`, `banking`, `telco` — **all with empty `knowledge: []`** |
| `common` | 146 | 6 buckets: `doc_gen`, `integrations`, `ux`, `dev_tools`, `session_mgmt`, `kb_mgmt` — each with `kbFiles[]` and `features[]` |
| `agentCore` | 330 | 8 always-on capability blurbs |
| `presets` | 368 | `lite/tech/domain/project/custom` — boolean flags + `target_platform` |
| `targetPlatform`| 415 | enum `host-a \| copilot` |

**It is the source of truth only for the heavy JSX** (heavy.jsx:15 imports it). Zero overlap with text-menu items.

Bugs in the registry:
- **`Core_Tool_Config.py` listed twice** in `ux.kbFiles` (lines 209-211). Copy-paste error.
- `domains.energy.knowledge = []` despite the actual `kb/bus/energy/` containing the dictionary and Domain KB. Stale.
- `presets[].target_platform: "host-a"` hardcoded — but heavy.jsx:188 also hardcodes the initial state. Two hardcoded copies that have to agree.

## 6. Light vs Heavy JSX have already drifted

**Heavy** (`Core_Tool_AgentConfigurator_heavy.jsx`):
- Proper React component
- **Imports the registry** at line 15 (`import REGISTRY_DEFAULT from './Core_KB_ConfiguratorRegistry.json'`)
- Renders every field: `knowledge[].updated`, `tools[].description`, `projectSources[].outputs`, `agentCore.features`, dark mode, progress steps
- **Driven by the JSON**

**Light** (`Core_Tool_AgentConfigurator_light.jsx`):
- Minified single-letter-var rewrite
- **Re-inlines the registry as a `const D = {tech:{salesforce:{…}}, dom:{…}, com:[…], core:[…], pre:[…]}` literal** at line 3
- Shortened labels (`"PULL — Metadata→Excel"` vs heavy's full strings)
- Light omits `kb[i].updated`, `kbPath`, `kbFiles`, `agentCore`, `targetPlatform.options`

Already drifted:
- Heavy reads `kb[i].updated`. Light has no `updated` field.
- Preset short keys differ: light uses `t/m/p`, registry uses `tech/domain/project`.
- Heavy calls `generate_agent_def(...)` with template-literal kwargs (heavy.jsx:233-234). Light calls it with a JSON-serialised object (light.jsx:15). **Two different signatures for the same backend function.**

## 7. `Core_Tool_Config.py` — near-stub, 🟡 design fragility

```python
# Core_Tool_Config.py:13-31
DEFAULT_CONFIG = {
 "agent": {"name": "Agent", "version": "0.1.0", "domain": "general"},
 "output": {"work_dir": _AGENT_WORK_DIR, "format": "markdown"}, # ← line 20: shared-globals reference
 "budget": {"max_tokens": 128_000, "warn_threshold": 0.75, "critical_threshold": 0.92},
 "requirements": {"pillars": None},
}
```

- Line 20 references `_AGENT_WORK_DIR` as a free variable inside a module-level dict literal. **In the agent runtime this works** — StorageAdapter has been exec'd before Config.py so `_AGENT_WORK_DIR` is in globals. **Raises `NameError` on a normal `import`** in a regular Python process. 🟡 design fragility — blocks rewrite, not a runtime bug.
- Even when it loads, the value is captured **at module-load time**. If `auto_configure` later updates `_AGENT_WORK_DIR` (Copilot path), the captured value is stale — this **is** a runtime concern (🟠) if anything other than `/mnt/data` is in play.
- The whole 63-LOC module amounts to: a default dict, `merge_config`, `resolve_config`. No coupling to the manifest, the menu, or the budget tracker. Looks like an early-draft stub.

## 8. Performance / context

- `load_menu` reads ~1.3 KB from the ZIP — cheap. Context cost is the dead 686 LOC the model has to load to find the function.
- `load_light_config` / `load_heavy_config` extract 8 KB / 33 KB JSX to disk every invocation with no cache check.
- Heavy JSX requires the model to keep the 10 KB registry JSON in context to reason about the generated config.
- Static menu could be inlined as a Python string constant — saves one ZIP open + decode + file IO per "show menu" call.

## 9. Top 5 file-line items for the rewrite

| # | Action | Files |
|---|---------------------------------------------------------------------------------------------------------------------|----------------------------------------------------|
| 1 | **One declarative menu/config source.** Single `menu.json` (or extend ConfiguratorRegistry) keyed by stable codes (G1, SF1, DEV, …) with `{label, desc, handler, area, requires}`. Build text menu, light JSX, heavy JSX, and resolver from it. | `Core_INSTR_Menu.md`, registry, both JSX, MenuBuilder |
| 2 | **Delete the 686 LOC of dead dynamic-menu code.** `AgentMenuBuilder.py:31-686`. | AgentMenuBuilder.py |
| 3 | **Have `light.jsx` import the same JSON `heavy.jsx` does.** Eliminate the inlined `const D = {...}`. Drop ~30% of light's bytes. Align both on one `generate_agent_def(...)` signature. | both JSX |
| 4 | **Fix the `_AGENT_WORK_DIR` capture.** Resolve via `os.environ.get("AGENT_WORK_DIR", "/mnt/data")` at call time, not module-load time. Same fix in Config.py. | AgentMenuBuilder.py:789, :865, Config.py:20 |
| 5 | **Merge `load_light_config` and `load_heavy_config` into one `load_config(variant)`.** They're 70 lines of near-duplicate. | AgentMenuBuilder.py:747-896 |

## 10. Spec ↔ code divergence summary

| MasterPrompt / Master_Ext claims | Code reality |
|-------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| §4: "Menu / Text menu / what can you do → `print(load_menu)`" | `load_menu` prints static markdown. Cannot reflect manifest state. |
| §4: "Simple Config / GUI → `r = load_light_config`" | Function `NameError`s on normal import. Inside the runtime: extracts JSX. |
| §4: "Full Config / Super GUI → `r = load_heavy_config`" | Same. |
| Master_Ext.md menu items reflect a `BUDGET` operation | `BUDGET` code has no link to `Core_Tool_Config.py`'s `DEFAULT_CONFIG.budget` |
| Menu is dynamic based on enabled areas | Static markdown — `areas[].enabled` ignored |
| One source of truth for what configurators offer | Three vocabularies + light.jsx re-inlines the registry |
| `presets[].target_platform` configurable | Both presets and heavy.jsx hardcode `"host-a"` |
