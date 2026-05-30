# Component Drilldown — Indexing Layer

**Files audited:**
- `kb/core/Indexing/Core_Tool_RebuildIndexes.py` (1241 LOC) — central tool
- `kb/core/Indexing/Core_Tool_RuntimeHelpers.py` (107 LOC) — runtime access helpers
- `kb/core/Indexing/Core_Tool_DomainStub.py` (62 LOC) — template stub (questionable inclusion)
- `kb/core/Indexing/Core_Instr_IndexingSystem.md` — spec
- `kb/core/Core_Instr_IndexWeighting.md` — spec for weighted L1
- `kb/core/Core_Index_L0.md`, `kb/core/Core_Index_L1.md` — generated artefacts

**Verdict:** the indexer is the single biggest source of "poor mapping/indexing". It cannot produce the indexes the spec describes. The L1 you see today (with rich D11 sub-sections) is **mostly hand-edited markdown**, not tool output.

**Severity legend** (see [`04_VERIFICATION.md`](../04_VERIFICATION.md) for full definitions):
🔴 = real runtime failure · 🟠 = works but produces bad UX · 🟡 = works in the agent runtime by design, blocks the rewrite.

Findings here are predominantly 🟠 (works but routes poorly) because the indexer runs to completion — it just produces inferior indexes than the spec promises. All findings hold in the agent runtime since they're about output quality, not invocation style.

---

## 1. Call graph reality

```
rebuild_all [RebuildIndexes.py:1185]
 └─ rebuild_manifest [:1200]
 └─ walk ZIP under kb/** [:240]
 └─ parse filename [:254]
 └─ derive load_strategy [:309-326]
 └─ _recalc_stats [:347] ← FIRST stats run (before L0/L1)
 ├─ (if project area) analyze_project_knowledge [:1212]
 ├─ (if project area) optimize_domain_structure [:1214]
 ├─ rebuild_indexes [:1219]
 │ ├─ _discover_domains [:388]
 │ ├─ _build_l0 [:389]
 │ └─ _build_l1 [:390]
 └─ inject_into_zip [:1224]
 ├─ add L0/L1 manifest entries by hand [:1126-1142]
 └─ _recalc_stats [:1151] ← SECOND stats run (different inputs)
 └─ full rewrite of ZIP [:1159-1169]
```

## 2. Why mapping is poor — six concrete bugs

### B1. Keyword/entity dictionaries are hardcoded, not derived
```python
# RebuildIndexes.py:437
_DOMAIN_KEYWORDS = {
 "Agent Framework": "schema|manifest|naming|convention|index|...",
 "Salesforce": "salesforce|SF|metadata|pull|push|...",
 ...
}
# RebuildIndexes.py:450
_DOMAIN_ENTITIES = {
 "Agent Framework": ["kb_schema", "kb_manifest", "L0", "L1", "L2"],
 ...
}
```
**No `IndexHints.md` is read anywhere in this module.** `IndexWeighting.md` section 4.3 explicitly tells builders to merge hint vocabulary, entity catalog, and routing rules. The code does none of that. `grep -i "IndexHint" Core_Tool_RebuildIndexes.py` returns zero matches.

### B2. Every project domain receives the same global entity list
```python
# RebuildIndexes.py:794-797 (inside optimize_domain_structure)
dom_entities = set
for ent_info in analysis.get("entities", []):
 dom_entities.add(ent_info["name"])
```
The loop iterates `analysis["entities"]` — the **global** entity list. It does not filter by which domain the entity belongs to. So every project domain in L0's ENTITY → DOMAIN map gets the top-50 entities of the entire KB. The "per file" filter implied by the surrounding code is a no-op.

### B3. L1 has zero weighting logic
```python
# RebuildIndexes.py:1051 (inside _build_l1)
for rtype in ["Tool", "Instr", "Index", "KB", "Tmpl"]:
 for resource in groups.get(rtype, []):
 lines.append(f"- `{resource['filename']}` …")
```
Same loop for every domain. Search the whole file for `P1`, `weight`, `priority` — zero hits. `IndexWeighting.md` says "P1 domains: list ALL resources with descriptions; P4–P5 domains: summary only." The code emits a uniform listing per domain.

**Implication:** the D11.1 / D11.2 / D11.3 / D11.4 / D11.5 / D11.6 sub-section structure you see in `Core_Index_L1.md` cannot come from `_build_l1`. Someone hand-wrote that. The next time `rebuild_all` runs, those sub-sections disappear and the section flattens to a single bullet list.

### B4. Topic vocabulary is Salesforce-flavored
```python
# RebuildIndexes.py:557-573
TOPIC_KEYWORDS = {
 "Lead Management": ["lead", "opportunity", "campaign", ...],
 "Account Management": ["account", "contact", "household", ...],
 "Service": ["case", "service", "ticket", ...],
 ...
}
```
A non-SF project (energy, banking, telco) will produce empty signals for every topic, fall through to `"General"`, and dump everything into a single `Project General` bucket at L0.

### B5. Domain IDs renumber across rebuilds
```python
# RebuildIndexes.py:934 (inside _build_l0)
default_order = overlay_prefix + ["Salesforce", "Agent Framework", "Document Generation", ...]
```
`overlay_prefix` is empty if there's no project KB, populated if there is. So the same `Salesforce` domain is `D03` after one rebuild and `D04` after another (when project domains exist). All references to `Dxx` in docs become stale across rebuilds.

### B6. Domain merge by word overlap
```python
# RebuildIndexes.py:751-754
for existing in merged:
 if {w for w in dom_name.split if len(w) > 3} & {w for w in existing.split if len(w) > 3}:
 merged[existing].extend(files)
 break
```
"Service Management" and "Order Management" share the word "Management" and silently get merged into one bucket. Deterministic but accidental.

## 3. Why rebuilds are slow

| Issue | Line | Cost |
|--------------------------------------------------------------|-------------------|-------------------------------------------------------------|
| Every file re-read, re-hashed every run | :240, :252 | `content_hash` is computed then never compared to prior |
| ZIP decompressed twice (rebuild_manifest + analyze) | :218, :497 | Linear waste |
| `_recalc_stats` called twice with different inputs | :347 + :1151 | Source of 119/93/98/92 drift in the manifest |
| `inject_into_zip` rewrites the whole ZIP | :1159-1169 | Python zipfile can't update in place — every change re-copies every byte |
| O(N²) overlay filter inside outer loop | :909-912 | For each overlay file, filters all groups |
| Regex pass over each text file (50 KB slice × 7 patterns) | :528-554 | Per file |

## 4. Why D04 looks different from D11 in the live L1

Searching the actual `Core_Index_L1.md`:
- D04 (Document Generation) — flat bullet list (matches `_build_l1` output exactly)
- D11 (Energy & Utilities) — has D11.1 / D11.2 / D11.3 / D11.4 / D11.5 / D11.6 sub-sections, query-routing hints table, freshness annotations

**`_build_l1` cannot produce sub-sections.** It iterates one resource_type bucket per domain. The D11 structure was written by hand. The next rebuild will overwrite it.

Confirms why query routing for Domain works well (hand-curated index) but for other domains falls back to keyword bingo.

## 5. RuntimeHelpers — fine, but tiny

`Core_Tool_RuntimeHelpers.py` (107 LOC) provides `load_from_zip`, `search_in_zip`, `list_zip`, `load_section`, `search_in_file`. These are the L2 access functions the MasterPrompt promises. They work. `load_section` uses heading match with substring-strip — fragile but functional. **Not the problem layer.**

One minor: `search_in_zip` reads files into memory one at a time, stops at `max_results=10` — fine. `load_section` truncates at `max_chars=8000` silently — caller has no way to know if section was truncated.

## 6. DomainStub — wrong place

`Core_Tool_DomainStub.py` (62 LOC) is a **template** with `# TODO` comments and an `example_operation` skeleton. It's intended to be copied into newly generated agents as their starting `*_Tool_Domain.py`. But it lives in the factory's own `kb/core/Indexing/` and is listed as a real Tool in D08 (Agent Framework).

This is the wrong location. It belongs in a `templates/` folder consumed by IdentityGen/AgentPackager, not the live KB. The L0/L1 index treats it as a real resource.

## 7. RebuildIndexes — top 6 file-line items for the rewrite

| # | File:Line | Action |
|---|----------------------------------------------------|---------------------------------------------------------------------------------------------------------|
| 1 | `RebuildIndexes.py:437-461` — static dicts | Delete. Replace with `IndexHints.md` reader + TF-IDF/frequency analysis of file content. |
| 2 | `RebuildIndexes.py:794-797` — global entity bleed | Replace with per-file-scope entity filter; entities tagged with their source file. |
| 3 | `RebuildIndexes.py:1017-1064` — flat `_build_l1` | Two-pass: classify weight P1–P5, emit hierarchical sub-sections for P1–P2, terse for P4–P5. |
| 4 | `RebuildIndexes.py:557-573` — SF-biased TOPIC_KEYWORDS | Replace with hints-derived domain vocabulary per project. |
| 5 | `RebuildIndexes.py:934` — overlay_prefix shifts IDs | Stable IDs from content hash or alphabetical order, not insertion order. |
| 6 | `RebuildIndexes.py:347 + :1151` — double stats | One `_recalc_stats` at the end. Or better: stats becomes a computed view, never persisted. |

## 8. Spec ↔ code divergence summary

| Spec promises | Code delivers |
|------------------------------------------------------------------------|--------------------------------------------------------------|
| Read `*_Instr_IndexHints.md` for each domain (IndexWeighting.md §4.3) | Never opens an IndexHints file |
| Merge analyzed entities + hint vocabulary + identity terms (§5.5b) | Static hardcoded dicts |
| P1 full detail → P5 summary in L1 (§2.1) | Uniform flat list per domain |
| L0 < 2K tokens; KEYWORD ROUTER per-domain distinguishing terms | Tries to fit, but vocab is frozen at code-time |
| Domain IDs stable across rebuilds (implied by every doc that says "D03 → SF") | Renumber depending on whether project KB exists |
| Validate L0 < 2K tokens (§2.3) | No size check; output not validated |
| Validate "every domain has at least one keyword" (§2.3) | No validation |
| Identity-driven customization (Identity §3.2: "enriched with project-specific terms") | No identity input read; identity terms hardcoded |
