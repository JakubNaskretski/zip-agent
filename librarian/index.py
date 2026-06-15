"""Search index — the cross-source entity bridge + full-text search, built as a
pure-Python, in-memory structure at open time from the live Knowledge Units.

Derived and rebuildable (I13) from all active KUs. **Source-agnostic**: every
source's KUs join here by shared entity names, which is what turns cross-source
questions into O(1) lookups ("which <source> items mention X?"). Today only
Salesforce feeds it; when Jira/Confluence/Mule land, they join automatically.

There is NO persisted index blob and NO sqlite: the index is a :class:`MemIndex`
held in memory only, assembled freshly from the manifest + kb/ files on every
``retrieve.open_index`` call. It holds three things:
  - ``entities`` — the bridge: ``(name, name_norm, ku_id, source, kind)`` tuples
  - ``docs`` + ``postings`` + ``df`` — an in-memory inverted index for BM25
    keyword/prose search over title/entities/body
  - ``aliases`` — ``(alias, canonical, via)`` triples for imprecise-name resolution

The alias set is DERIVED — never hand-maintained. Three provenance tiers (``via``):
  - ``"mech"`` — mechanical variants from every entity name in ``entities``
    (strip ``__c``/``__r``; CamelCase split; underscores→spaces; initials acronym
    when the name has ≥ 2 words; no-space join of spaced words)
  - ``"label"`` — display labels and ``label_<locale>`` attrs harvested from
    ``salesforce:graph/sf`` and ``mule:graph/mule`` graph KUs ONLY (never jira /
    confluence / docs)
  - ``"curated"`` — any manifest KU whose id starts with ``curated:glossary/``;
    its body is one alias per line; its ``entities`` list holds the canonical names

Because it is built fresh from the live files, the index is always current — there
is nothing to persist and nothing to churn. ``rebuild_indexes()`` is kept as a
no-op only for backward compatibility.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .digest._progress import EVERY as _EVERY
from .schema import content_hash

INDEX_ID = "agent:index/search"
INDEX_PATH = "kb/indexes/search.sqlite"
_FTS_SKIP_KINDS = {"index", "graph"}        # don't full-text the derived blobs
_BODY_CAP = 200_000

# Graph KU ids from which label aliases are harvested (ONLY these two — never
# jira/confluence/docs, per the entity-bridge source restriction).
_LABEL_GRAPH_IDS = ("salesforce:graph/sf", "mule:graph/mule")

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

# Full-text tokenizer (mirrors the old FTS5 unicode61 word-splitting closely
# enough for our needs): word characters, lowercased.
_TOK = re.compile(r"\w+", re.UNICODE)


def _toks(text):
    """Lowercased word tokens of ``text`` (``[]`` for empty/None)."""
    return [t.lower() for t in _TOK.findall(text or "")]


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


@dataclass
class MemIndex:
    """In-memory search index built from the live KB (no persistence, no sqlite).

    Attributes
    ----------
    entities:
        ``(name, name_norm, ku_id, source, kind)`` tuples — the cross-source
        entity bridge.
    docs:
        one dict per searchable KU::

            {"ku_id", "source", "title", "path", "tf": Counter, "dl": int}

    df:
        ``token -> number of docs containing it`` (document frequency).
    postings:
        ``token -> list[doc_index]`` (inverted index into ``docs``).
    N:
        number of docs.
    avgdl:
        mean document length (token count) across ``docs``.
    aliases:
        ``(alias, canonical, via)`` triples for imprecise-name resolution.
    logical:
        a content hash of the logical KB state (ids + entities + content_hash).
    """
    entities: list = field(default_factory=list)
    docs: list = field(default_factory=list)
    df: "Counter" = field(default_factory=Counter)
    postings: dict = field(default_factory=dict)
    N: int = 0
    avgdl: float = 0.0
    aliases: list = field(default_factory=list)
    logical: str = ""


def build_index(lib, progress=None) -> MemIndex:
    """Assemble a :class:`MemIndex` from the current live KB state.

    Pure Python: scans the manifest, reads each searchable KU body from the
    kb/ files, and builds the entity bridge, the inverted index for BM25, and
    the derived alias set. Nothing is persisted.

    ``progress`` (callable, e.g. ``print``): one-line count every ``EVERY`` KUs
    during the dominant body-indexing loop, plus a compact final line."""
    mi = MemIndex()
    sig = []
    ent_names = set()
    indexed = 0
    for ku in sorted(lib.manifest.all(), key=lambda k: k.id):
        if ku.status != "active" or ku.id == INDEX_ID:
            continue
        ents = list(ku.entities or [])
        for ent in ents:
            mi.entities.append((ent, ent.lower(), ku.id, ku.source, ku.kind))
            ent_names.add(ent)
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
                toks = _toks(ku.title) + _toks(" ".join(ents)) + _toks(body)
                tf = Counter(toks)
                idx = len(mi.docs)
                mi.docs.append({"ku_id": ku.id, "source": ku.source,
                                "title": ku.title, "path": ku.path,
                                "tf": tf, "dl": len(toks)})
                for t in tf:
                    mi.postings.setdefault(t, []).append(idx)
                    mi.df[t] += 1
        sig.append([ku.id, sorted(ents), ku.content_hash])
        indexed += 1
        if progress is not None and indexed % _EVERY == 0:
            progress(f"index rebuild: {indexed} KUs indexed")
    if progress is not None and indexed > 0:
        progress(f"index rebuild: done — {indexed} KUs indexed")

    mi.N = len(mi.docs)
    mi.avgdl = (sum(d["dl"] for d in mi.docs) / mi.N) if mi.N else 0.0

    # ---- derive aliases ----
    names = ent_names
    aset: set = set()   # (alias, canonical, via) — deduplicate

    # (a) mechanical aliases for every distinct entity name
    for name in names:
        for alias in _mech_aliases(name):
            aset.add((alias, name, "mech"))

    # (b) label aliases from salesforce:graph/sf and mule:graph/mule only —
    #     emitted only when the canonical exists in the entity bridge
    for alias, canonical, via in _label_aliases(lib):
        if canonical in names:
            aset.add((alias, canonical, via))

    # (c) curated glossary aliases — canonicals trusted as-is, no pre-existence check
    for alias, canonical, via in _curated_aliases(lib):
        aset.add((alias, canonical, via))

    mi.aliases = sorted(aset)
    mi.logical = content_hash(json.dumps(sig, sort_keys=True, ensure_ascii=False))
    return mi


def rebuild_indexes(lib, author, rationale, progress=None):
    """No-op kept for backward compatibility.

    The search index is now built in memory from the live KB on every
    ``retrieve.open_index`` call, so there is nothing to persist and nothing to
    rebuild. This commits an EMPTY transaction (no KU written, no generation
    bump) and returns its ok Report — so existing ``rebuild_indexes(...)`` call
    sites keep working unchanged. Signature preserved (``progress`` accepted and
    ignored)."""
    try:
        return lib.begin(author, rationale).commit()
    except Exception:   # pragma: no cover — empty commit cannot fail today
        # Fall back to constructing a minimal ok Report exactly as commit()
        # returns for an empty change set (no rows): ok, no churn.
        from .librarian import Report
        rep = Report()
        rep.committed_generation = lib.manifest.generation
        return rep
