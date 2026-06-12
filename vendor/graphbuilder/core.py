"""Source-agnostic graph-building framework.

The build runs in two passes so a missing resolver or a dangling reference can
never obstruct the rest of the graph:

  1. EXTRACT — every file is offered to the extractors; the first one that
     ``handles`` it emits ``nodes`` + *raw edges*. A raw edge names its target
     logically as ``(to_kind, to_name)`` rather than as a node id, so extractors
     never need to know which ids exist.
  2. RESOLVE — each raw edge's ``(to_kind, to_name)`` is handed to the resolver
     for that kind, which returns a concrete node id (and may add an external-stub
     node for a target outside the repo). Unresolvable references — and edges
     whose kind has no resolver — land in ``result["unresolved"]`` rather than
     raising; per-extractor failures land in ``result["errors"]``.

The result is always ``{"nodes", "edges", "unresolved", "errors"}``. The core
knows nothing about Salesforce or any other source.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol, TypeVar, runtime_checkable

_T = TypeVar("_T")


def node(nid, ntype, label=None, **attrs) -> dict:
    """Build a node dict. The label defaults to the id's name segment
    (everything after the first ``/``), e.g. ``object/Account`` -> ``Account``."""
    return {"id": nid, "type": ntype, "label": label or nid.split("/", 1)[-1], **attrs}


def raw_edge(src, etype, to_kind, to_name) -> dict:
    """Build an edge whose target is named logically as ``(to_kind, to_name)``.
    The concrete destination id is filled in during pass 2 (resolve)."""
    return {"src": src, "type": etype, "to_kind": to_kind, "to_name": to_name}


@runtime_checkable
class Extractor(Protocol):
    source: str
    def handles(self, path: Path) -> bool: ...
    def extract(self, path: Path) -> tuple[list[dict], list[dict]]:
        """Return (nodes, raw_edges). Must not raise on a bad ref — skip it."""


@runtime_checkable
class Resolver(Protocol):
    kind: str
    def resolve(self, name: str, registry: dict):
        """Return a node id for (kind, name); may add an external-stub node to
        `registry`. Return None if it genuinely can't be resolved (the edge is
        reported in ``unresolved``), or False to DROP the edge silently — for
        schema-aware resolvers that recognize a reference as parser noise
        (a platform/system name, a field token misread as an object)."""


class GraphBuilder:
    """Owns the extractor list and resolver registry, and runs the two-pass build.

    Use the fluent registration methods, then ``build``:

        GraphBuilder().register(*extractors).register_resolver(*resolvers).build(repo)
    """

    def __init__(self):
        self.extractors: list[Extractor] = []
        self.resolvers: dict[str, Resolver] = {}

    def register(self, *extractors):
        """Add one or more extractors. Returns self for chaining."""
        self.extractors.extend(extractors)
        return self

    def register_resolver(self, *resolvers):
        """Register one or more resolvers, keyed by their ``kind``. A later
        resolver for the same kind replaces an earlier one. Returns self."""
        for r in resolvers:
            self.resolvers[r.kind] = r
        return self

    def build(self, repo) -> dict:
        """Run extract-then-resolve over every file in ``repo``.

        Returns ``{"nodes", "edges", "unresolved", "errors"}``. Nothing here
        raises: a file no extractor handles is skipped, an extractor that throws
        is logged to ``errors``, and an edge that can't be resolved is logged to
        ``unresolved``.
        """
        repo = Path(repo)
        paths = sorted(p for p in repo.rglob("*") if p.is_file())
        return self.build_files(paths, root=repo)

    def build_files(self, paths, root=None) -> dict:
        """Run the same two-pass build over an explicit iterable of files.

        Identical contract to :meth:`build`, but you choose the files — used to
        digest a single file (or any subset) without scanning a whole tree. Files
        no extractor handles are skipped; order doesn't affect the result.

        The two passes are also public on their own — :meth:`extract_files` /
        :meth:`resolve_extracted` — for callers that need the per-file extraction
        results (e.g. a digest building one record per source file) without
        paying for a second extraction pass.
        """
        extracted, errors = self.extract_files(paths, root=root)
        return self.resolve_extracted(extracted, errors)

    def extract_files(self, paths, root=None) -> tuple:
        """Pass 1 only: offer each file to the extractors and keep the raw
        results per file. Returns ``(extracted, errors)`` where ``extracted`` is
        ``[(Path, nodes, raw_edges), …]`` for every handled file (in input
        order) and ``errors`` is the build-level error list. Nothing raises: an
        unhandled file is skipped, a throwing extractor becomes an error entry.

        Every emitted node is stamped with ``source_path`` — the file it came
        from, relative to ``root`` when given (POSIX separators), else the path
        as passed — so a graph node can always be traced back to its base
        source. An extractor that sets its own ``source_path`` (e.g. a
        decomposed child file) wins, relativized the same way; for a node
        emitted from several files the registry keeps the first file seen
        (matching node-registry first-wins)."""
        root = Path(root) if root is not None else None
        extracted: list[tuple] = []
        errors: list[dict] = []
        for path in paths:
            path = Path(path)
            extractor = next(
                (e for e in self.extractors if _safe(lambda: e.handles(path), False)),
                None,
            )
            if extractor is None:
                continue
            try:
                nodes, edges = extractor.extract(path)
            except Exception as exc:  # one bad file/extractor must not kill the build
                errors.append({
                    "source": getattr(extractor, "source", "?"),
                    "path": str(path.name),
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            src = _source_path(path, root)
            for n in nodes or []:
                sp = n.get("source_path")
                # extractor-set paths win but get the same relativization (an
                # already-relative value passes through _source_path unchanged)
                n["source_path"] = src if sp is None else _source_path(Path(sp), root)
            extracted.append((path, nodes or [], edges or []))
        return extracted, errors

    def resolve_extracted(self, extracted, errors=None) -> dict:
        """Pass 2: registry + resolution over :meth:`extract_files` output.
        Returns the usual ``{"nodes", "edges", "unresolved", "errors"}``."""
        # `registry` maps node id -> node dict; the first node seen for an id
        # wins (setdefault), so duplicate emissions are harmless. A node marked
        # ``partial: True`` is an ATTRIBUTE DONOR (e.g. a translation file
        # contributing `label_pl` to a field defined elsewhere): it never claims
        # the node's identity attrs (label/source_path), it only fills attrs the
        # real node doesn't have. A partial with no real counterpart stays in
        # the graph still flagged ``partial`` — the thing it annotates was not
        # retrieved.
        registry: dict[str, dict] = {}
        pending_edges: list[dict] = []
        _IDENTITY = ("id", "type", "label", "source_path", "partial")
        for _, nodes, edges in extracted:
            for n in nodes:
                cur = registry.get(n["id"])
                if cur is None:
                    registry[n["id"]] = n
                elif n.get("partial") or cur.get("partial") == n.get("partial"):
                    # donor arriving second, or same standing: fill missing attrs
                    for k, v in n.items():
                        if k not in _IDENTITY:
                            cur.setdefault(k, v)
                else:
                    # real node arriving after a donor: it takes over, keeping
                    # the donor's contributed attrs as fill
                    for k, v in cur.items():
                        if k not in _IDENTITY:
                            n.setdefault(k, v)
                    registry[n["id"]] = n
            pending_edges.extend(edges)

        resolved_edges: list[dict] = []
        unresolved: list[dict] = []
        for edge in pending_edges:
            kind = edge.get("to_kind")
            resolver = self.resolvers.get(kind)
            if resolver is None:
                unresolved.append({**edge, "reason": f"no resolver for kind '{kind}'"})
                continue
            try:
                dst = resolver.resolve(edge["to_name"], registry)
            except Exception as exc:  # a bad resolver must not kill the build
                unresolved.append({**edge, "reason": f"resolver error: {exc}"})
                continue
            if dst is False:   # schema-aware drop: recognized parser noise
                continue
            if dst is None:
                unresolved.append({**edge, "reason": "unresolved target"})
            else:
                resolved_edges.append({"src": edge["src"], "dst": dst, "type": edge["type"]})

        return {
            "nodes": list(registry.values()),
            "edges": resolved_edges,
            "unresolved": unresolved,
            "errors": list(errors or []),
        }


def _source_path(path: Path, root) -> str:
    """The traceability value stamped on nodes: ``path`` relative to ``root``
    (POSIX separators, for cross-OS determinism) when it lies under it, else the
    path as given."""
    if root is not None:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:  # outside the root — keep the full path
            pass
    return path.as_posix()


def _safe(fn: Callable[[], _T], default: _T) -> _T:
    """Call ``fn`` and return its result, or ``default`` if it raises. Keeps a
    misbehaving ``handles`` from aborting the whole file scan."""
    try:
        return fn()
    except Exception:
        return default
