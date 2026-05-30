"""KU schema + validators — the single source of the controlled vocabulary.

Invariant I4: one validator, one regex, no second copy anywhere. Every write
path in the Librarian validates through :func:`validate_ku`.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

# ---- controlled vocabularies (one place; invariant I4) ----
TIERS = {"raw", "structured", "indexes", "curated", "built-in"}
SOURCES = {"jira", "confluence", "mule", "salesforce", "domain", "agent"}
KINDS = {
    "source-record",   # raw: a Jira issue, Confluence page, Mule/SF source file
    "graph",           # structured: a derived graph file (mule/sf)
    "doc",             # structured: a normalized doc record
    "index",           # indexes: an sqlite / routing artifact
    "curated-note",    # curated: agent-authored knowledge
    "standard",        # curated/built-in: a best-practice KB
    "tool",            # built-in: the scraper, etc.
    "instruction",     # built-in: protocol/persona docs
}
CONFIDENCE = {"VERIFIED", "VERY_LIKELY", "LIKELY", "UNVERIFIED"}
STATUS = {"active", "superseded", "retired"}
LINK_KINDS = {"references", "derived-from", "supersedes", "contradicts", "child-of"}

# id namespaces: a data source, or "curated" for agent-authored notes
ID_NAMESPACES = SOURCES | {"curated"}
_ID_RE = re.compile(r"^([a-z][a-z0-9_-]*):(\S.*)$")

# tier -> required path prefix (keeps each tier in its own lane)
TIER_PREFIX = {
    "raw": "kb/raw/",
    "structured": "kb/structured/",
    "indexes": "kb/indexes/",
    "curated": "kb/curated/",
}
# built-in content is allowed in several places
BUILTIN_PREFIXES = ("kb/domain/", "kb/standards/", "tools/", "reference/", "librarian/")


def content_hash(body) -> str:
    """Stable sha256 of a body (str or bytes). Load-bearing for I9 (idempotent
    re-ingest) — the digest compares this to skip unchanged records."""
    if isinstance(body, str):
        body = body.encode("utf-8")
    elif body is None:
        body = b""
    return hashlib.sha256(body).hexdigest()


@dataclass
class KnowledgeUnit:
    """The atom of memory. Exactly one manifest entry per KU (see §2.3)."""

    id: str
    kind: str
    tier: str
    source: str
    path: str
    title: str = ""
    entities: list = field(default_factory=list)
    links: list = field(default_factory=list)        # [{"kind": ..., "to": ...}]
    provenance: dict = field(default_factory=dict)
    freshness: dict = field(default_factory=dict)
    confidence: str = "UNVERIFIED"
    content_hash: str = ""
    status: str = "active"                            # active | superseded | retired
    review_needed: bool = False                       # set when a derived-from source changes (§9)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeUnit":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


def parse_id(ku_id: str):
    """Return (namespace, rest) or None if the id is malformed."""
    m = _ID_RE.match(ku_id or "")
    return (m.group(1), m.group(2)) if m else None


def validate_ku(ku: KnowledgeUnit) -> list:
    """Return a list of human-readable errors; an empty list means valid (I4)."""
    errs: list[str] = []

    parsed = parse_id(ku.id)
    if parsed is None:
        errs.append(f"id {ku.id!r} is not of the form '<namespace>:<rest>'")
    else:
        ns, _ = parsed
        if ns not in ID_NAMESPACES:
            errs.append(f"id namespace {ns!r} not in {sorted(ID_NAMESPACES)}")
        elif ns == "curated" and ku.source != "agent":
            errs.append("curated ids must have source 'agent'")
        elif ns in SOURCES and ku.source != ns:
            errs.append(f"id namespace {ns!r} must match source {ku.source!r}")

    if ku.kind not in KINDS:
        errs.append(f"kind {ku.kind!r} not in {sorted(KINDS)}")
    if ku.tier not in TIERS:
        errs.append(f"tier {ku.tier!r} not in {sorted(TIERS)}")
    if ku.source not in SOURCES:
        errs.append(f"source {ku.source!r} not in {sorted(SOURCES)}")
    if ku.confidence not in CONFIDENCE:
        errs.append(f"confidence {ku.confidence!r} not in {sorted(CONFIDENCE)}")
    if ku.status not in STATUS:
        errs.append(f"status {ku.status!r} not in {sorted(STATUS)}")

    # path lane
    if ku.tier in TIER_PREFIX:
        if not ku.path.startswith(TIER_PREFIX[ku.tier]):
            errs.append(f"{ku.tier} KU path {ku.path!r} must start with {TIER_PREFIX[ku.tier]!r}")
    elif ku.tier == "built-in":
        if not ku.path.startswith(BUILTIN_PREFIXES):
            errs.append(f"built-in KU path {ku.path!r} must start with one of {BUILTIN_PREFIXES}")

    # links well-formed
    for ln in ku.links:
        if not isinstance(ln, dict) or "kind" not in ln or "to" not in ln:
            errs.append(f"link {ln!r} must be a dict with 'kind' and 'to'")
        elif ln["kind"] not in LINK_KINDS:
            errs.append(f"link kind {ln['kind']!r} not in {sorted(LINK_KINDS)}")

    return errs
