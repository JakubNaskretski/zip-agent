# profiles/ — the agent factory

One shared engine, many agent **profiles**. Each profile is *data*, never engine
code: a prompt overlay plus an optional knowledge seed. The engine
(`librarian/`), the vendored parsers (`vendor/graphbuilder/`), the schema and the
13 invariants are identical in every built agent — a profile only changes what the
agent *is told to do* and what it *knows at birth*.

## Layout

```
profiles/
├── _base/MASTER_PROMPT.md   # the shared operating contract, with {{PROFILE_*}} markers
├── <name>/
│   ├── profile.json         # {name, title, description, overlays}
│   ├── prompt/              # overlay fragments (optional, per marker slot)
│   │   ├── intro.md         # → {{PROFILE_INTRO}}      (persona specialization)
│   │   ├── operations.md    # → {{PROFILE_OPERATIONS}} (extra operations, e.g. §4.2 DISCOVER)
│   │   └── cheatsheet.md    # → {{PROFILE_CHEATSHEET}} (extra §8 cheat-sheet lines)
│   └── seed/                # optional born-knowing KB content (see caveat below)
```

A missing fragment leaves its marker empty. `project` ships no overlays, so its
built prompt is the base contract verbatim; `rfp` adds the DISCOVER operation.

## Build

```bash
python3 scripts/build_memory.py --list-profiles
python3 scripts/build_memory.py --profile rfp           # → dist/rfp/{memory.zip, MASTER_PROMPT.md}
python3 scripts/build_memory.py --profile project       # → dist/project/{memory.zip, MASTER_PROMPT.md}
```

Each build emits a **clean** `memory.zip` (engine + any seed; no ingested data
yet) and the **assembled** `MASTER_PROMPT.md` *beside* it — the prompt is never
bundled inside the ZIP. To deploy: paste `MASTER_PROMPT.md` into the host's
instructions field and upload `memory.zip`. The org/Jira/docs knowledge is
digested into that instance at runtime; instances do not share a KB.

## Adding a profile (e.g. a sector builder)

Drop a `profiles/<name>/` folder with a `profile.json` and whatever overlay
fragments differ. No engine change, no code branch — the profile registry is
derived from this directory. New *capability* code (if ever needed) lives in the
shared `librarian/` engine and is invoked by the prompt, never copied per profile.

## Lifecycle

- **Upgrade an existing agent's engine without losing its KB:**
  `scripts/upgrade_memory.py OLD_memory.zip NEW_code.zip -o upgraded.zip`
  (carries `kb/**` + `manifest.json` + `dev/` forward; the search index is built in memory at query time on this branch, so there is no first-boot rebuild step).
- **Extract / back up an agent's KB:**
  `scripts/extract_kb.py DEPLOYED_memory.zip -o kb-bundle.zip`.

Profiles are distinct lineages — there is deliberately **no** profile↔profile
migration (you cannot turn an RFP agent's memory into a project agent's).

## Seed caveat (current)

`seed/` content is copied verbatim into the ZIP. Files placed under `kb/` are not
yet registered as Knowledge Units (that needs a Librarian-ingest step at build
time), so for now profiles ship their conventions in the **prompt**, not as
pre-loaded KB. Keep `seed/` empty until the build grows a seed-ingest step.
