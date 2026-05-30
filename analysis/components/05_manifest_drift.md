# Component Drilldown — Manifest & Metadata Drift Forensics

**Files inspected:**
- `agent_manifest.json` (1913 lines, top-level + 119 resource entries)
- `kb/core/Core_Index_L0.md`
- `kb/core/Core_Index_L1.md`
- `kb/dev/DEV_KB_Chronicle.md`

**Verdict:** the manifest is internally inconsistent across **four different self-counts** and **three different version strings**, with **two parallel area-naming systems** and **eight overlapping `load_strategy` values**. This is what the user perceives as "poor mapping" — the indexes, stats, and resource list are four different views of the same KB that have drifted apart.

**Severity:** every finding here is 🔴 + 🟡 — the agent reads inconsistent JSON at runtime (real bug; 22 resources invisible to L0/L1 routing), and the multiple-schemas problem blocks any rewrite that tries to enforce one source of truth. These are facts on disk; severity doesn't depend on invocation style.

---

## 1. Three different version numbers in the manifest

```jsonc
{
 "version": "1.0.26", // top-level
 "agent_version": "1.0.23", // top-level
 "metadata": {
 "version": "1.0.24", // nested
 ...
 }
}
```

Plus the folder name says `AgentDefinition_v1.0.26`. Plus `Core_Index_L1.md` line 2 says `_Generated: 2026-03-19 | v1.0.16_` — **stale by 10 versions**. Plus `Core_Index_L0.md` line 2 says `_Generated: 2026-04-01 | v1.0.26_` (the most recent).

## 2. Three different timestamps

```jsonc
{
 "updated": "2026-03-26T11:50:00Z", // top-level
 "updated_at": "2026-04-01T22:00:51.471396+00:00", // top-level
 "metadata": {
 "last_updated": "2026-03-27T11:15:42.208936" // nested
 }
}
```

The `updated_at` (April 1) post-dates `updated` (March 26) by 6 days — they were written by different code paths at different times. Same problem as the version strings.

## 3. Four different counts of "how many resources are there"

| Source | Count | Notes |
|-------------------------------------|------:|--------------------------------------------------------|
| `resources[]` (top-level array) | **119** | Authoritative — actual ZIP contents |
| `stats.total_resources` | 93 | Computed by `_recalc_stats`. Not refreshed. |
| `metadata.resource_count` | 98 | Set by yet another code path. Most recent of the stats.|
| `indexes.core.resources_at_build` | 92 | What was true when L0/L1 was last built |

Root cause (from Audit #1 + my manifest inspection): `Core_Tool_RebuildIndexes.py:347` runs `_recalc_stats` once during `rebuild_manifest` (before L0/L1 are added), then again at `:1151` (after). Plus another writer (AgentExtend / KBManifest / mystery v2-with-load_strategy writer) updates `metadata.resource_count` independently.

Practical impact: **22 resources are invisible to L1.** They were added after the L1 was last built and the rebuild never re-ran.

## 4. Two parallel area-naming systems

Top-level `areas{}` dict uses slashed paths:
```jsonc
"areas": {
 "business/energy": { "path": "kb/bus/energy", "enabled": true },
 "tech/salesforce": { "path": "kb/tech/salesforce", "enabled": true },
 ...
}
```

Resource `area` fields use shorthand:
```
Bus_ENRG_KB_PL_Dictionary.md area: "business"
Bus_ENRG_KB_Domain_Messages.json area: "Bus_ENRG" ← different casing, different format
Tech_SF_Tool_Pull.py area: "tech" ← misses "/salesforce"
Common_Tool_Budget.py area: "common" ← matches top-level
Core_Tool_StartupLoader.py area: "core" ← matches top-level
Dev_KB_Changelog.json area: "dev" ← matches top-level
Dev_SF_KB_DynamicContext.md area: "dev/salesforce" ← slashed, matches top-level
```

Breakdown:
```
tech: 37 (should be "tech/salesforce")
core: 32 ✓
common: 22 ✓
Bus_ENRG: 20 (should be "business/energy")
dev: 7 ✓
business: 1 (should be "business/energy" too?)
```

So `Tech_SF_*` resources can't be looked up by `areas["tech/salesforce"].path`, and `Bus_ENRG_*` resources can't be looked up by `areas["business/energy"].path`. **Querying the manifest by area is broken** unless you know which naming style each resource happens to use.

## 5. Chaotic `load_strategy` vocabulary

Eight different values appear in the same manifest:

| Value | Count | Inferred meaning |
|---------------------|------:|-----------------------------------------------|
| `extract_to_disk` | 52 | extract to filesystem |
| `read_to_variable` | 28 | read into Python variable |
| `python_var` | 15 | (synonym of above?) |
| `read_to_context` | 12 | print into conversation context |
| `null` | 5 | not set |
| `context` | 4 | (synonym of `read_to_context`?) |
| `disk_exec` | 2 | extract + `exec` (Tool variant) |
| `extract_and_exec` | 1 | (synonym of `disk_exec`) |

Per Audit #3, **none of these values exist as valid values in any of the three known writers' code**. KBSchema validates only `LOAD_MODES = {on_startup, on_demand}` (KBSchema.py:31-34). AgentExtend writes `load_strategy=auto`. The `extract_to_disk`/`read_to_variable`/`read_to_context` strings come from a writer we couldn't pinpoint (likely RebuildIndexes:309-326 or an older Factory release).

This means **StartupLoader's type-aware routing has to fall back on `resource_type`** because `load_strategy` is unreliable.

## 6. Two `load_*` fields, semi-overlapping

```jsonc
"load_mode": "on_demand", // {on_startup, on_demand}
"load_strategy": "read_to_variable" // one of the eight above
```

KBSchema only knows `load_mode`. The "mystery writer" introduced `load_strategy` as parallel metadata. They overlap: `on_startup` mostly maps to `read_to_context`, `on_demand` mostly maps to the rest. But:
- 22 resources have `load_mode: null` AND `load_strategy: <something>`.
- 5 resources have `load_strategy: null` AND `load_mode: "on_demand"`.

So neither field is reliable on its own.

## 7. Field-completeness drift

```
import_alias set: 1 / 119 resources (the recipe demands this)
api set: 0 / 119 resources (the recipe demands this too)
load_mode set: 97 / 119
load_strategy set: 114 / 119
description set: 119 / 119
content_hash set: 119 / 119
```

`import_alias` and `api` are theoretical contracts. The recipe's STEP 1 inline bootstrap reads `r["import_alias"]` (GenerationRecipe.md:51) — for 118 of 119 entries, returns nothing.

## 8. `indexes` field is sparse and stale

```jsonc
"indexes": {
 "tech/salesforce": { "last_built": "2026-03-18T13:44:38.455690" },
 "core": {
 "l0": "kb/core/Core_Index_L0.md",
 "l1": "kb/core/Core_Index_L1.md",
 "last_built": "2026-03-18T13:44:38.500248",
 "resources_at_build": 92
 }
}
```

Two issues:
- `tech/salesforce` has only `last_built` — no `l0`/`l1` paths. Does Salesforce have its own indexes or not? Code doesn't agree (some tools look for `Tech_SF_Index_*.md`, some don't).
- `resources_at_build: 92` is the freshest count we have. 27 resources have been added since (119 - 92), but `indexes.core.last_built` shows March 18 — i.e. nothing has rebuilt since.

## 9. The `metadata` envelope vs top-level fields

The manifest has a `metadata{}` object with its own `version`, `last_updated`, `resource_count` — duplicating top-level fields. This looks like a v1 → v2 migration artefact (AgentExtend's v1 manifest had a `metadata{}` envelope; KBManifest's v2 has top-level fields). Both ended up coexisting.

## 10. The 22 resources with `load_mode: None`

All but 2 are Domain (`Bus_ENRG_*`):

```
Bus_ENRG_Instr_Domain_AgentPrompt.md type=Instr strat=context
Bus_ENRG_Instr_Domain_DocumentationGuide.md type=Instr strat=context
Bus_ENRG_Tool_Domain_TSKBParser.py type=Tool strat=disk_exec
Bus_ENRG_KB_Domain_Messages.json type=KB strat=python_var
Bus_ENRG_KB_Domain_DataTypes.json type=KB strat=python_var
Bus_ENRG_KB_Domain_CrossRef.json type=KB strat=python_var
Bus_ENRG_KB_Domain_SWI.json type=KB strat=python_var
Bus_ENRG_KB_Domain_IRiESP.json type=KB strat=python_var
Bus_ENRG_Instr_Domain_IndexHints.md type=Instr strat=python_var
... 11 more Domain files ...
Tech_SF_Instr_CodeGen_Explain.md type=Instr strat=None
Core_Instr_IndexWeighting.md type=Instr strat=context
```

Adding Domain was clearly a manual `AgentExtend.add_knowledge_source` operation — the entries use `load_strategy` (v1 vocab) without `load_mode` (v2 vocab). KBManifest's `migrate_from_v1` was never run.

`Tech_SF_Instr_CodeGen_Explain.md` and `Core_Instr_IndexWeighting.md` were added even more recently, missing both fields entirely. They're orphans.

## 11. Top 6 rewrite priorities for the manifest

| # | Action | Where |
|---|---------------------------------------------------------------------------------------------------------------------|------------------------------------|
| 1 | **`stats` becomes a computed view.** Never persisted. Generated on read from `resources[]`. Eliminates the 4-way count drift instantly. | RebuildIndexes.py:347, :1151; KBManifest._recalc_stats |
| 2 | **One version, one updated_at.** Drop `agent_version`, `metadata.version`, `metadata.last_updated`. Bump via one path. | manifest top level |
| 3 | **One area-naming system.** Pick `tech/salesforce` style (slashed). Resource `area` matches `areas{}` key. One-shot migrator for old shorthands. | every resource entry |
| 4 | **One `load_strategy` vocabulary** with 3 values: `extract` / `variable` / `context`. Drop `load_mode` (merge into `load_strategy`). Provide migrator. | every resource entry |
| 5 | **`import_alias` and `api` become required for `resource_type=Tool`** at release time, or get removed from spec. | KBSchema validators |
| 6 | **Single writer**. KBManifest's API is the only allowed mutation path. RebuildIndexes/AgentExtend/AgentPackager delegate. Schema version check at every read. | KBManifest.py, all callers |

## 12. Quick-sanity Python snippet (to verify drift in future)

```python
import json
m = json.load(open("agent_manifest.json"))

# Should print 4 identical numbers:
print("array: ", len(m["resources"]))
print("stats.total_resources: ", m["stats"]["total_resources"])
print("metadata.resource_count:", m["metadata"]["resource_count"])
print("indexes.core.resources_at_build:", m["indexes"]["core"]["resources_at_build"])

# Should print one version, one timestamp:
print("version, agent_version, metadata.version:",
 m.get("version"), m.get("agent_version"), m["metadata"]["version"])
print("updated, updated_at, metadata.last_updated:",
 m.get("updated"), m.get("updated_at"), m["metadata"]["last_updated"])

# Should be empty:
from collections import Counter
strats = Counter(r.get("load_strategy") for r in m["resources"])
print("load_strategy vocab:", dict(strats))
```

Today, none of those invariants hold.
