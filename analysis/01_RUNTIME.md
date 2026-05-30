# Runtime Mechanics — Bootstrap, Loading, Indexing, Context Budget

This file documents how a host model actually *runs* an agent: what happens on session start, how each resource type is loaded, the L0/L1/L2 indexing system, and the rules that protect the context window.

> Source files driving this layer:
> - `MasterPrompt.md` §3, §3a, §6, §7
> - `kb/core/Core_Tool_StartupLoader.py` (v3.0)
> - `kb/core/Core_Instr_ContextProtection.md`
> - `kb/core/Indexing/Core_Instr_IndexingSystem.md`
> - `kb/core/Core_Tool_StorageAdapter.py`
> - `kb/core/Core_Instr_DualPlatform.md`

---

## 1. Bootstrap — what happens on session start

### 1.1 Entry point

```python
# Host-A (code interpreter)
exec(open("/mnt/data/Core_Tool_StartupLoader.py").read)
result = load_startup_resources("/mnt/data/AgentDefinition.zip")

# GitHub Copilot (filesystem; no Python interpreter)
# Static Markdown auto-loaded from .github/copilot-instructions.md;
# the agent reads kb/core/Core_Index_L0.md via read_file on first question.
```

### 1.2 What `load_startup_resources` does

1. Loads `Core_Tool_StorageAdapter.py` **first** — monkey-patches `zipfile.ZipFile` so subsequent code can transparently treat ZIPs and unpacked folders the same way. Sets `_AGENT_WORK_DIR` per platform (`/mnt/data` for Host-A, `./output` for Copilot).
2. Reads `agent_manifest.json` from the ZIP root.
3. For each resource marked `on_startup`, applies **type-aware routing** (§3 below).
4. Returns a `StartupResult` with:
 - `.context` — dict of resources the host SHOULD print into the conversation
 - `.variables` — dict of resources that MUST stay in Python memory only
 - `.extracted` — list of file paths written to disk
 - `.manifest` — parsed manifest dict
 - `.platform` — `"host-a"` or `"copilot"`

### 1.3 What loads on startup (from the manifest)

| Resource | Destination | Why |
|-------------------------------------|-------------|---------------------------------------------------------|
| `Core_Instr_Master_Ext.md` | 📋 CONTEXT | Active extension of the master prompt |
| `Core_Instr_ContextProtection.md` | 📋 CONTEXT | Always-active rules |
| `Core_Instr_IndexingSystem.md` | 📋 CONTEXT | Needed to perform L0→L1→L2 routing |
| `Core_INSTR_Menu.md` | 🐍 VARIABLE | Printed only when user asks for menu |
| `Core_Instr_DevProtocol.md` | 🐍 VARIABLE | Loaded to context only when DEV mode activates |
| `Core_Instr_DualPlatform.md` | 🐍 VARIABLE | Loaded only when platform-specific behaviour is queried |
| `Core_Tool_StartupLoader.py` | 💾 DISK | Extracted, exec'd; never enters context |
| `Core_Index_L0.md` + `Core_Index_L1.md` | 📋 CONTEXT | Routing tables (loaded shortly after startup) |

## 2. The three destinations — and why it matters

Every resource lands in **exactly one** place:

| Destination | Mechanism | Context impact |
|---------------------------|------------------------------------------|----------------|
| 💾 **DISK** (`_AGENT_WORK_DIR`) | `zf.extract(...)` | ❌ none |
| 🐍 **PYTHON VARIABLE** | `zf.read(...)` → string in code-interpreter memory | ❌ none |
| 📋 **CONTEXT WINDOW** | Printed/returned into the conversation | ⚠️ consumes tokens |

The contract is strict: **never print tool source or large KB content into context**. The agent reads them in code and prints only the distilled answer.

## 3. Type-aware routing matrix

| `resource_type` | Where it goes | Action |
|-----------------|----------------------------|-----------------------------------------------------|
| `Tool` (.py) | 💾 DISK | Extract → `exec`; functions become callable |
| `Tool` (.jsx) | 💾 DISK | Extract → load as artifact (Host-A only) |
| `Tmpl` (.docx/.pptx) | 💾 DISK | Extract → pass path to generators |
| `Instr` active | 📋 CONTEXT | Read → print to conversation |
| `Instr` conditional | 🐍 VARIABLE | Read → hold until the mode that needs it is active |
| `KB` ≤ 5 KB | 📋 CONTEXT (if needed) | Read → print |
| `KB` > 5 KB | 🐍 VARIABLE | Read → search/filter in code → print only fragments |
| `Index` | 📋 CONTEXT | Read → print (small routing tables) |

## 4. On-demand loading algorithm

```
1. LOOKUP — find resource in manifest by filename or purpose
2. CHECK — read resource_type, size_kb, ext
3. BUDGET — will it fit within the remaining non-reserved budget?
4. ROUTE — apply the matrix above
5. REGISTER — mark loaded in _loaded_resources set (idempotent)
```

`is_loaded(name)` and `mark_loaded(name)` prevent double-loading.

## 5. The manifest (`agent_manifest.json`)

A single JSON inventory at the ZIP root. Each `resources[]` entry carries enough metadata for StartupLoader to route it without reading the file:

```json
{
 "filename": "Common_Tool_Budget.py",
 "path": "kb/common/Common_Tool_Budget.py",
 "area": "common",
 "domain": null,
 "resource_type": "Tool",
 "purpose": "Budget",
 "ext": ".py",
 "load_mode": "on_demand", // or "on_startup"
 "size_kb": 3.48,
 "content_hash": "52e61d583bd3a15acddaf1ff185a24ac",
 "description": "Common_Tool_Budget.py — Token Budget Tracker v1.0",
 "added": "2026-03-18T13:44:38",
 "updated": "2026-03-18T13:44:38",
 "load_strategy": "extract_to_disk" // or "read_to_variable" / "read_to_context"
}
```

Top-level fields: `manifest_version`, `agent_name`, `agent_version`, `created`, `updated`, `areas{}` (enabled flags), `resources[]`. Manifest version is `2.0`.

## 6. Indexing — L0 / L1 / L2

The agent's knowledge can be far larger than its context window, so it is navigated via a 3-layer hierarchical index defined in `Core_Instr_IndexingSystem.md`.

### 6.1 L0 NANO INDEX (`Core_Index_L0.md`, < 2 K tokens — always in context)

Contains four routing tables, all small:

- **DOMAINS** — `Dxx` ID, name, description, source counts per category
- **ENTITY → DOMAIN MAP** — `EntityName` → `Dxx`
- **KEYWORD ROUTER** — regex-friendly OR-groups → `Dxx`
- **EXTERNAL SYSTEMS MAP** / **PROCESS MAP** (optional)

Example from this package:
```
- `salesforce|SF|metadata|pull|push|diff|audit|SF1|SF2|...|apex|...` → D03
- `energy|utilities|ENRG|Domain|UNK|komunikat|PL-|CK-|...` → D11
```

### 6.2 L1 QUICK INDEX (`Core_Index_L1.md`, 5–15 K tokens — loaded at init or first query)

One section per domain. For each domain it lists Tools / Instructions / KB / Indexes with **filename + 1-line description + path + size**. Plus a STATS block at the top.

This is what lets the agent answer "which file do I need?" without opening anything.

### 6.3 L2 SOURCE DATA (the actual `kb/` files — never fully loaded)

Accessed via runtime helpers (see `kb/core/Indexing/Core_Tool_RuntimeHelpers.py`):

```python
load_from_zip(zip_path, inner_path) # single file in-memory
search_in_zip(zip_path, keyword) # cross-file keyword grep
load_section(source, header) # one Markdown section by heading
list_zip(zip_path, pattern='') # filename listing
is_loaded(name) # idempotency check
```

### 6.4 Question-answering flow

```
Step 0 CLASSIFY — type (Q&A / GEN / DIAG / DEV), domains, complexity
Step 1 FAST-PATH — answerable from already-loaded context? general knowledge? single read?
Step 2 ROUTE via L0 — keyword/entity → domain ID(s)
Step 3 PLAN via L1 — domain → specific files + sections + token cost
Step 4 LOAD L2 — ONLY the sections identified in step 3
Step 5 SYNTHESIZE — answer + sources + confidence tier
```

## 7. Context budget (§7 of master prompt)

| Complexity | Max L2 tokens | Max files |
|------------|--------------:|----------:|
| Simple | 20 K | 3 |
| Medium | 40 K | 6 |
| Complex | 60 K | 10 |
| Max | 80 K | 15 |

**Rules**
- Never load an entire large file — always by section.
- Never skip the index — always route L0 → L1 → L2.
- Never reload — check `is_loaded` first.
- Reserve **≥ 30 %** of the context window for reasoning and output.
- Compress proactively: after each step, rewrite findings as a compact bullet list and drop the raw data.

## 8. Context protection rules (§6 of master prompt)

Working cycle: **READ → NOTE → DISCARD → THINK → repeat.** Never hold multiple large sources open simultaneously. Before each tool call: "will the result fit within remaining non-reserved budget?" If not — compress first, then call.

Specific anti-patterns called out:

| ❌ Don't | ✅ Do instead |
|--------------------------------------|--------------------------------------------------------|
| Dump raw files into context | Process in code interpreter, return distilled output |
| Read before probing | First get size/line count/headings, then decide |
| Paste two sources to compare in head | Write code that emits a diff or compliance checklist |
| Extract ZIPs to disk to read | `load_from_zip` in memory |
| Load all L2 "just in case" | Stay within the complexity-based token cap |
| Re-search the same concept | Check disk / earlier results first |

## 9. Confidence model (§8 of master prompt)

Every answer carries a confidence tier derived from **freshness × source tier**.

**Freshness:** Current ≤ 6 mo · Recent 6–12 mo · Relatively recent 1–3 yr · Dated 3–6 yr · Old 6–15 yr · Archival > 15 yr.

**Source tiers (web):** T1 official / legislative · T2 major industry · T3 blogs · T4 other.
**Source tiers (project KB):** T1 source code / metadata / system config · T2 approved user stories or docs · T3 in-development artefacts · T4 emails / unclear.

**Resulting level:**

| Level | Trigger | Style |
|-------------------|---------------------------------------------------------------------|----------------------|
| ✅ VERIFIED | T1 Current/Recent Project KB or T1 Current/Recent web result | Stated as fact |
| 🟡 VERY LIKELY | Other T1 / T2 Project KB; T1 Relatively recent web; T2 Current web | Stated as fact |
| 🟠 LIKELY | T3/T4 Project KB; other T2/T3 web | Caveat + link |
| 🔴 UNVERIFIED | No confirmation / conflicting / T4 web | Explicit disclaimer |

## 10. Conflict management (§5 of master prompt)

Priority order when conflicting answers arise across areas:

```
Project > Domain > Technology > Common
```

Complementary content from multiple areas is merged. Contradictory content follows the order above, and the agent must **tell the user** which areas conflicted and which one won.

## 11. Dual-platform runtime (Host-A vs GitHub Copilot)

The same `kb/` tree is consumed differently on the two supported hosts:

| Aspect | Host-A | GitHub Copilot |
|--------------------------------|------------------------------------------|-------------------------------------------|
| Agent definition | `AgentDefinition*.zip` | Folder in the repository |
| Instructions loading | `MasterPrompt.md` pasted into Instructions; startup via `exec` | `.github/copilot-instructions.md` auto-loaded |
| Python tools | `exec`'d into memory | Run via `run_in_terminal` (`python3 tool.py …`) |
| KB access | `zf.read(...)` to Python variable | `read_file` reads `.md`/`.json` directly |
| Work directory | `/mnt/data/` | `./output/` |
| JSX configurators | ✅ supported | ❌ not supported |
| Persistent state between turns | ✅ Python namespace | ❌ stateless |

`Core_Instr_DualPlatform.md` is held as a Python variable on startup and only printed when platform-specific behaviour matters.

## 12. Storage adapter (ZIP ↔ folder transparency)

`Core_Tool_StorageAdapter.py` is loaded *before* anything else by StartupLoader. It monkey-patches `zipfile.ZipFile` so that code written for a ZIP layout works identically when the agent has been deployed as an unpacked folder (Copilot case). This is what makes "same `kb/` tree, two platforms" possible without forking the tools.

---

Continue to [`02_KB_AREAS.md`](02_KB_AREAS.md) for the full tool/instruction catalog, area by area.
