"""Default resolvers — turn a (kind, name) reference into a node id.

`StubResolver` resolves to an existing node if present, else creates an
**external stub** node (marked `external: True`) so the edge still forms — this
is how referenced-but-not-retrieved targets (standard/packaged objects, managed
classes) appear without obstructing the graph.

A reference whose `to_kind` has **no registered resolver** is reported in
`result["unresolved"]` (with the missing kind) — that's the signal to add one.
"""
from __future__ import annotations

from .confluence.parse import page_ref
from .model import NODE_TYPES


class StubResolver:
    """Resolve (kind, name) → `kind/name`; create an external stub if unseen."""

    def __init__(self, kind: str, stub: bool = True):
        self.kind = kind
        self.stub = stub

    def resolve(self, name: str, registry: dict) -> str | None:
        nid = f"{self.kind}/{name}"
        if nid in registry:
            return nid
        if not self.stub:
            return None
        registry[nid] = {"id": nid, "type": self.kind, "label": name, "external": True}
        return nid


class LabelResolver:
    """Resolver for custom labels that normalizes the reference prefix before
    matching, so the various ways code names a label all reach the one node:

        $Label.Foo · System.Label.Foo · Label.Foo · c.Foo · ns.Foo  ->  label/Foo

    Apex, Visualforce, Flow and LWC each prefix labels differently; the label's
    metadata fullName is the bare name, so we strip a known keyword prefix and any
    leading namespace segment, then match (or stub) ``label/<name>``.
    """

    kind = "label"
    _PREFIXES = ("$Label.", "System.Label.", "Label.")

    def resolve(self, name: str, registry: dict) -> str | None:
        bare = name
        for p in self._PREFIXES:
            if bare.startswith(p):
                bare = bare[len(p):]
                break
        if "." in bare:                       # drop a leading namespace, e.g. c.Foo -> Foo
            bare = bare.split(".", 1)[1]
        nid = f"label/{bare}"
        if nid in registry:
            return nid
        registry[nid] = {"id": nid, "type": "label", "label": bare, "external": True}
        return nid


class PageResolver:
    """Resolver for Confluence pages, whose nodes are **id-keyed**
    (``page/<pageId>``) while storage-format links can only name their target as
    ``<space>/<title>`` (that is all ``<ri:page>`` markup carries). Maps the title
    form back to the collected page's id-keyed node via a (space/title) -> id
    index over the registry; an unseen target becomes a title-keyed external stub
    (its real id is unknowable without its dump) — exactly like an off-repo
    Salesforce reference.
    """

    kind = "page"

    def __init__(self):
        self._index: dict[str, str] = {}
        self._indexed_at = -1  # registry size the index was built at

    def _title_index(self, registry: dict) -> dict[str, str]:
        # Collected (non-external) pages all exist before resolution starts; the
        # registry only grows by external stubs during it, so rebuilding whenever
        # the size changed keeps the index correct at trivial cost.
        if len(registry) != self._indexed_at:
            index: dict[str, str] = {}
            for nid, n in registry.items():
                if n.get("type") != "page" or n.get("external") or not n.get("space_key"):
                    continue
                # first writer wins, mirroring the node registry's setdefault
                index.setdefault(page_ref(n["space_key"], n.get("label", "")), nid)
            self._index = index
            self._indexed_at = len(registry)
        return self._index

    def resolve(self, name: str, registry: dict) -> str | None:
        nid = f"page/{name}"
        if nid in registry:                       # a stub created earlier this pass
            return nid
        hit = self._title_index(registry).get(name)
        if hit:
            return hit
        registry[nid] = {"id": nid, "type": "page", "label": name, "external": True}
        return nid


# Apex platform types whose qualified calls (`String.valueOf(…)`,
# `JSON.serialize(…)`, `Database.executeBatch(…)`) are language plumbing, not
# org structure — a name-only parser can't tell, so the SCHEMA-AWARE resolver
# drops them at resolve time UNLESS the org really declares a class by that name
# (shadowing wins). Lower-case for comparison. Deliberately heads-of-qualified-
# calls only: interface names used by `implements`/`async` edges (Queueable,
# Batchable, Comparable…) are NOT listed — those edges are designed structure.
APEX_SYSTEM_TYPES = frozenset({
    "system", "database", "schema", "test", "json", "string", "math", "limits",
    "userinfo", "apexpages", "messaging", "http", "httprequest", "httpresponse",
    "date", "datetime", "time", "decimal", "integer", "long", "double",
    "boolean", "id", "blob", "crypto", "encodingutil", "url", "pagereference",
    "label", "type", "list", "set", "map", "search", "approval", "eventbus",
    "trigger", "site", "network", "auth", "cache", "matcher", "pattern",
    "exception", "assert", "restcontext", "restrequest", "restresponse",
})


class ApexMethodResolver:
    """Schema-aware resolver for qualified Apex calls (``Qual.method``).

    A declared method (``apexmethod/Qual.method`` exists, cross-file included)
    resolves to the real node. A qualifier that is a known platform type — and
    NOT shadowed by a real class in the org — is parser noise (``String.valueOf``
    is not a dependency) and the edge is DROPPED. Anything else stubs, exactly
    like the plain StubResolver did.
    """

    kind = "apexmethod"

    def __init__(self):
        self._class_lower: set = set()
        self._indexed_at = -1   # registry size the index was built at

    def _classes(self, registry: dict) -> set:
        # Apex is case-insensitive: `string.write()` shadowed by a declared
        # class `String` must not be dropped. Declared (non-external) classes
        # all exist before resolution starts (cf. ObjectResolver._tails).
        if len(registry) != self._indexed_at:
            self._class_lower = {
                nid.split("/", 1)[1].lower()
                for nid, n in registry.items()
                if n.get("type") == "apexclass" and not n.get("external")
            }
            self._indexed_at = len(registry)
        return self._class_lower

    def resolve(self, name: str, registry: dict):
        nid = f"apexmethod/{name}"
        hit = registry.get(nid)
        if hit is not None and not hit.get("external"):
            return nid
        qual = name.split(".", 1)[0]
        if qual.lower() in APEX_SYSTEM_TYPES \
                and qual.lower() not in self._classes(registry):
            return False                           # platform call — drop
        if nid in registry:                        # an earlier stub for this name
            return nid
        registry[nid] = {"id": nid, "type": "apexmethod", "label": name,
                         "external": True}
        return nid


class ObjectResolver:
    """Schema-aware resolver for sObject references.

    A name-only Apex/SOQL scan can't tell a custom FIELD token from an object:
    ``Total__c`` in code emits a (correct) field read AND a spurious
    ``object/Total__c`` reference. At resolve time the org schema is in the
    registry — so a ``__c`` name that is NOT a declared object but IS a declared
    field's name is recognized as that noise and the edge is DROPPED. Real
    objects resolve; everything else stubs (standard/off-repo objects keep
    appearing as externals, unchanged).
    """

    kind = "object"

    def __init__(self):
        self._field_tails: set = set()
        self._object_lower: set = set()
        self._indexed_at = -1   # registry size the index was built at

    def _index(self, registry: dict):
        # Declared (non-external) fields/objects all exist before resolution
        # starts; the registry only grows by external stubs during it, so
        # rebuilding when the size changed keeps the index correct at trivial
        # cost (cf. PageResolver). Lower-cased: Apex/SOQL are case-insensitive,
        # so the drop decision must be too — in both directions (a differently-
        # cased field token still drops; a differently-cased declared OBJECT
        # still protects).
        if len(registry) != self._indexed_at:
            tails: set = set()
            objs: set = set()
            for nid, n in registry.items():
                if n.get("external"):
                    continue
                if n.get("type") == "field" and "." in nid:
                    tails.add(nid.rsplit(".", 1)[-1].lower())
                elif n.get("type") == "object":
                    objs.add(nid.split("/", 1)[1].lower())
            self._field_tails = tails
            self._object_lower = objs
            self._indexed_at = len(registry)

    def _tails(self, registry: dict) -> set:
        self._index(registry)
        return self._field_tails

    def resolve(self, name: str, registry: dict):
        nid = f"object/{name}"
        hit = registry.get(nid)
        if hit is not None and not hit.get("external"):
            return nid
        low = name.lower()
        if low.endswith("__c") and low in self._tails(registry) \
                and low not in self._object_lower:
            return False                           # a field token, not an object
        if nid in registry:                        # an earlier stub for this name
            return nid
        registry[nid] = {"id": nid, "type": "object", "label": name,
                         "external": True}
        return nid


# Every node kind gets an external stub when a target isn't in the repo, EXCEPT
# the kinds with dedicated resolvers: `label` (prefix normalization), `page`
# (title -> page-id mapping), and the schema-aware `object` / `apexmethod`
# (platform-noise suppression). Derived from the single node vocabulary so a new
# type can never be left without a resolver.
_DEDICATED_KINDS = {"label", "page", "object", "apexmethod"}
STUB_KINDS = sorted(NODE_TYPES - _DEDICATED_KINDS)


def default_resolvers() -> list:
    # the dedicated resolvers above, plain stubs for every other kind
    return [StubResolver(k) for k in STUB_KINDS] + [
        LabelResolver(), PageResolver(), ObjectResolver(), ApexMethodResolver()]
