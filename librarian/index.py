"""Search index — the cross-source entity bridge + full-text search, built as a
single serialized SQLite KU.

Derived and rebuildable (I13) from all active KUs. **Source-agnostic**: every
source's KUs join here by shared entity names, which is what turns cross-source
questions into O(1) lookups ("which <source> items mention X?"). Today only
Salesforce feeds it; when Jira/Confluence/Mule land, they join automatically.

The index is one `.sqlite` file holding three tables:
  - `entities(name, name_norm, ku_id, source, kind)` — the bridge
  - `docs` FTS5 over (title, entities, body) — keyword/prose search
  - `aliases(alias TEXT, canonical TEXT, via TEXT)` — imprecise-name resolution

The alias table is DERIVED and REBUILDABLE (I13) — never hand-maintained.
Three provenance tiers (``via``):
  - ``"mech"`` — mechanical variants from every entity name in ``entities``
    (strip ``__c``/``__r``; CamelCase split; underscores→spaces; initials acronym
    when the name has ≥ 2 words; no-space join of spaced words)
  - ``"label"`` — display labels and ``label_<locale>`` attrs harvested from
    ``salesforce:graph/sf`` and ``mule:graph/mule`` graph KUs ONLY (never jira /
    confluence / docs)
  - ``"curated"`` — any manifest KU whose id starts with ``curated:glossary/``;
    its body is one alias per line; its ``entities`` list holds the canonical names

It is stored as the body of a single KU (`agent:index/search`, tier=indexes) and
rebuilt via `rebuild_indexes()`. Idempotent: a logical hash of the KB state drives
the no-op, so an unchanged KB doesn't churn the index.
"""
from __future__ import annotations

import json
import re
import sqlite3
import tempfile
from pathlib import Path

from .digest._progress import EVERY as _EVERY
from .schema import KnowledgeUnit, content_hash

INDEX_ID = "agent:index/search"
INDEX_PATH = "kb/indexes/search.sqlite"
_FTS_SKIP_KINDS = {"index", "graph"}        # don't full-text the derived blobs
_BODY_CAP = 200_000

# Graph KU ids from which label aliases are harvested (ONLY these two — never
# jira/confluence/docs, per the entity-bridge source restriction).
_LABEL_GRAPH_IDS = ("salesforce:graph/sf", "mule:graph/mule")

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _norm(text: str) -> str:
    """Normalize an alias candidate: lowercase, collapse whitespace."""
    return " ".join(text.lower().split())


def _mech_aliases(name: str):
    """Yield (alias, canonical) mechanical variants for one entity name.

    Rules (in order):
    1. Strip trailing __c / __r (yields both the stripped form and further
       variants from it).
    2. CamelCase split to spaced words (``ServicePoint`` → ``service point``).
    3. Underscores → spaces (catches snake_case names).
    4. Spaced-words-joined (``service point`` → ``servicepoint``).
    5. Initials acronym — ONLY when the name splits into ≥ 2 words
       (``ServicePoint`` → ``sp``; a single-word name gets no acronym).

    All variants are normalized (lowercase, single-spaced). Empties and
    single-character aliases are dropped. Aliases identical to lower(canonical)
    are also dropped (already covered by entities.name_norm exact lookup).
    """
    canonical = name
    baseline = name

    # step 1 — strip trailing __c / __r
    stripped = None
    for suffix in ("__c", "__r"):
        if baseline.lower().endswith(suffix):
            stripped = baseline[:-len(suffix)]
            break

    # collect the name forms we will split / join
    forms = [baseline]
    if stripped:
        forms.append(stripped)

    seen: set = set()
    skip = _norm(canonical)

    def _emit(alias: str):
        a = _norm(alias)
        if a and len(a) > 1 and a != skip and a not in seen:
            seen.add(a)
            yield a

    for form in forms:
        # step 2 — CamelCase split
        words_from_camel = _CAMEL_RE.sub(" ", form).split()
        camel_spaced = " ".join(words_from_camel)
        yield from _emit(camel_spaced)

        # step 3 — underscores → spaces (after stripping trailing suffix)
        underscore_spaced = form.replace("_", " ")
        yield from _emit(underscore_spaced)

        # combine: camel then underscore → strip underscores from camel split
        # (covers e.g. Some_ObjectName__c properly)
        combined_words = re.split(r"[_\s]+", camel_spaced)
        combined_spaced = " ".join(w for w in combined_words if w)
        yield from _emit(combined_spaced)

        # step 4 — spaced-words joined (only if spaced form has spaces)
        if " " in combined_spaced:
            joined = combined_spaced.replace(" ", "")
            yield from _emit(joined)

        # step 5 — initials acronym, only when ≥ 2 words
        words = [w for w in combined_spaced.split() if w]
        if len(words) >= 2:
            acronym = "".join(w[0] for w in words if w)
            yield from _emit(acronym)


def _label_aliases(lib):
    """Yield (alias, canonical, "label") triples from the two graph KUs.

    Canonical = name segment of the node id (text after the last "/").
    Only emitted when that canonical is in the entities table (checked by
    caller).  Never raises — missing / malformed graph body is silently skipped.
    """
    for graph_id in _LABEL_GRAPH_IDS:
        body = lib.read_body(graph_id)
        if body is None:
            continue
        try:
            data = json.loads(body)
        except Exception:
            continue
        nodes = data.get("nodes", [])
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if not isinstance(node, dict):
                continue
            nid = node.get("id", "")
            canonical = nid.split("/", 1)[-1] if "/" in nid else nid
            if not canonical:
                continue
            # emit label
            label = node.get("label", "")
            if label:
                alias = _norm(label)
                if alias and len(alias) > 1 and alias != _norm(canonical):
                    yield alias, canonical, "label"
                # also emit no-space join of the label if it has spaces
                if " " in alias:
                    joined = alias.replace(" ", "")
                    if joined and len(joined) > 1 and joined != _norm(canonical):
                        yield joined, canonical, "label"
            # emit label_<locale> attrs
            for key, val in node.items():
                if not key.startswith("label_"):
                    continue
                if not isinstance(val, str) or not val.strip():
                    continue
                loc_alias = _norm(val)
                if loc_alias and len(loc_alias) > 1 and loc_alias != _norm(canonical):
                    yield loc_alias, canonical, "label"
                if " " in loc_alias:
                    joined = loc_alias.replace(" ", "")
                    if joined and len(joined) > 1 and joined != _norm(canonical):
                        yield joined, canonical, "label"


def _curated_aliases(lib):
    """Yield (alias, canonical, "curated") triples from curated:glossary/* KUs.

    Canonical names come from the KU's ``entities`` list.  The body is parsed
    as one alias per line (empty lines and lines starting with ``#`` skipped).
    Canonicals from the glossary tier are trusted as-is and do NOT need to
    pre-exist in the entities table.
    """
    for ku in lib.manifest.all():
        if ku.status != "active":
            continue
        if not ku.id.startswith("curated:glossary/"):
            continue
        canonicals = list(ku.entities or [])
        if not canonicals:
            continue
        body = lib.read_body(ku.id)
        if body is None:
            continue
        try:
            text = body.decode("utf-8", "replace")
        except Exception:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            alias = _norm(line)
            if not alias or len(alias) <= 1:
                continue
            for canonical in canonicals:
                if alias != _norm(canonical):
                    yield alias, canonical, "curated"


def _serialize(con) -> bytes:
    try:
        return con.serialize()              # Python 3.11+, SQLite w/ deserialize
    except (AttributeError, sqlite3.OperationalError):   # pragma: no cover
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tf:
            tmp = Path(tf.name)
        dst = sqlite3.connect(tmp)
        con.backup(dst)
        dst.close()
        data = tmp.read_bytes()
        tmp.unlink(missing_ok=True)
        return data


def load_sqlite(data: bytes):
    """Open serialized index bytes as an in-memory connection."""
    con = sqlite3.connect(":memory:")
    try:
        con.deserialize(data)               # Python 3.11+
    except (AttributeError, sqlite3.OperationalError):   # pragma: no cover
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tf:
            tmp = Path(tf.name)
        tmp.write_bytes(data)
        disk = sqlite3.connect(tmp)
        disk.backup(con)
        disk.close()
        tmp.unlink(missing_ok=True)
    return con


def build_index(lib, progress=None):
    """Return (sqlite_bytes, logical_hash) for the current KB state.

    ``progress`` (callable, e.g. ``print``): one-line count every ``EVERY``
    KUs during the dominant FTS population loop, plus a compact final line."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE entities(name TEXT, name_norm TEXT, ku_id TEXT, source TEXT, kind TEXT)")
    con.execute("CREATE INDEX ix_ent ON entities(name_norm)")
    # contentless FTS: postings only — bodies already live as kb/ files, and a
    # stored copy roughly tripled the index (observed 40 MB on a 7k-KU org).
    # docmap carries the rowid -> KU identity that contentless tables can't.
    con.execute("CREATE VIRTUAL TABLE docs USING fts5("
                "title, entities, body, content='', tokenize='unicode61')")
    con.execute("CREATE TABLE docmap(rowid INTEGER PRIMARY KEY, ku_id TEXT, "
                "source TEXT, title TEXT, path TEXT)")
    # alias table: imprecise-name resolution (derived, never hand-maintained)
    con.execute("CREATE TABLE aliases(alias TEXT, canonical TEXT, via TEXT)")
    con.execute("CREATE INDEX ix_alias ON aliases(alias)")
    sig = []
    indexed = 0
    for ku in sorted(lib.manifest.all(), key=lambda k: k.id):
        if ku.status != "active" or ku.id == INDEX_ID:
            continue
        ents = list(ku.entities or [])
        for ent in ents:
            con.execute("INSERT INTO entities VALUES(?,?,?,?,?)",
                        (ent, ent.lower(), ku.id, ku.source, ku.kind))
        if ku.kind not in _FTS_SKIP_KINDS:
            try:
                raw = lib.store.read(ku.path)
            except (OSError, FileNotFoundError):
                raw = b""
            if b"\x00" in raw[:4096]:
                raw = b""   # binary body (original docx/xlsx/pdf bytes) — its
                            # text sidecar KU is the search surface, not junk tokens
            body = raw.decode("utf-8", "replace")[:_BODY_CAP]
            if body or ents or ku.title:
                cur = con.execute("INSERT INTO docs(title, entities, body) VALUES(?,?,?)",
                                  (ku.title, " ".join(ents), body))
                con.execute("INSERT INTO docmap VALUES(?,?,?,?,?)",
                            (cur.lastrowid, ku.id, ku.source, ku.title, ku.path))
        sig.append([ku.id, sorted(ents), ku.content_hash])
        indexed += 1
        if progress is not None and indexed % _EVERY == 0:
            progress(f"index rebuild: {indexed} KUs indexed")
    if progress is not None:
        if indexed % _EVERY != 0 and indexed > 0:
            progress(f"index rebuild: done — {indexed} KUs indexed")
        elif indexed > 0:
            progress(f"index rebuild: done — {indexed} KUs indexed")

    # ---- populate aliases ----
    # Collect the set of distinct canonical entity names for the mech pass
    # and the set for the label-alias membership check.
    all_names_rows = con.execute("SELECT DISTINCT name FROM entities").fetchall()
    all_names = {r[0] for r in all_names_rows}

    alias_pairs: set = set()   # (alias, canonical, via) — deduplicate

    # (a) mechanical aliases for every distinct entity name
    for name in all_names:
        for alias in _mech_aliases(name):
            alias_pairs.add((alias, name, "mech"))

    # (b) label aliases from salesforce:graph/sf and mule:graph/mule only
    for alias, canonical, via in _label_aliases(lib):
        # only emit if the canonical exists in entities (set-membership check)
        if canonical in all_names:
            alias_pairs.add((alias, canonical, via))

    # (c) curated glossary aliases — canonicals trusted as-is, no pre-existence check
    for alias, canonical, via in _curated_aliases(lib):
        alias_pairs.add((alias, canonical, via))

    con.executemany("INSERT INTO aliases VALUES(?,?,?)", alias_pairs)

    con.commit()
    data = _serialize(con)
    con.close()
    logical = content_hash(json.dumps(sig, sort_keys=True, ensure_ascii=False))
    return data, logical


def rebuild_indexes(lib, author, rationale, progress=None):
    """Build the search index and commit it as a derived KU. Idempotent when the
    KB is unchanged (the logical hash drives the no-op). Returns the Report.

    ``progress`` (callable, e.g. ``print``): one-line count every ``EVERY``
    KUs during the FTS population loop, plus a compact final line."""
    data, logical = build_index(lib, progress=progress)
    ku = KnowledgeUnit(
        id=INDEX_ID, kind="index", tier="indexes", source="agent",
        path=INDEX_PATH, title="Search index (entity bridge + FTS)",
        confidence="VERIFIED", content_hash=logical,
    )
    txn = lib.begin(author, rationale)
    txn.ingest_ku(ku, body=data)
    return txn.commit()
