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


# Every node kind gets an external stub when a target isn't in the repo, EXCEPT
# `label` (LabelResolver: prefix normalization) and `page` (PageResolver: title ->
# page-id mapping). Derived from the single node vocabulary so a new type can
# never be left without a resolver.
STUB_KINDS = sorted(NODE_TYPES - {"label", "page"})


def default_resolvers() -> list:
    # `label` and `page` get their dedicated resolvers, not plain stubs.
    return [StubResolver(k) for k in STUB_KINDS] + [LabelResolver(), PageResolver()]
