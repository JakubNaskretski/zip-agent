"""Apex class extractor (`*.cls`).

Emits an `apexclass/<Class>` node plus `apexmethod/<Class>.<method>` nodes and the
edges between them: `contains`, method `calls` (intra-class, qualified, and
instance-typed), `extends`/`implements`, `reads`/`writes` -> object/field (SOQL
and DML), `references` -> object (custom objects, `__mdt`/settings accessors),
`async` (Batchable/Queueable/Schedulable/`@future` and call-site enqueue), and a
`tests` edge for `@IsTest` classes. Method/class `annotations` and the class
`async_kind`/`kind` are recorded as node attributes. Every reference is
best-effort: an odd or broken match is skipped, never raised.

Two interchangeable backends back this extractor:

  - `_regex.extract_regex` — the always-available regex/string parse; the
    guaranteed fallback, depending only on the stdlib plus the Salesforce parser.
  - `_ast.extract_ast` — an optional tree-sitter backend that is strictly more
    precise (no comment/string false positives, instance-call resolution via a
    local symbol table). Used only when the apex grammar loads
    (`_APEX_PARSER is not None`); otherwise the regex backend runs. It is a
    superset: every edge the regex backend emits is also emitted here.

Install the AST backend with `pip install graph-builder[ast]` (adds `tree-sitter`
+ `tree-sitter-language-pack`). When absent, `_APEX_PARSER` stays `None` and the
regex path is taken. Shared constants/helpers live in `_common`.
"""
from __future__ import annotations

from pathlib import Path

from ...core import raw_edge
from ...salesforce import _strip_apex
from ._ast import extract_ast as _extract_ast_impl
from ._common import _LABEL_REF
from ._regex import extract_regex as _extract_regex_impl

# --- optional tree-sitter AST backend; regex fallback ----------------------- #
# Guard both the import and the grammar load: if tree-sitter or the apex grammar
# is unavailable, ``_APEX_PARSER`` stays ``None`` and the regex backend is used.
# Never raises at import time. This module-level flag is authoritative —
# ``_dispatch`` gates on it and ``_extract_ast`` passes it into the AST backend,
# so monkeypatching it to None forces the regex fallback.
_APEX_PARSER = None
try:  # pragma: no cover - exercised by whichever backend is installed
    from tree_sitter_language_pack import get_parser as _get_parser

    try:
        _APEX_PARSER = _get_parser("apex")
    except Exception:
        _APEX_PARSER = None
except Exception:
    _APEX_PARSER = None


def _ast_api_supported(parser) -> bool:
    """True iff ``parser`` exposes the method-style node API this backend uses.

    The AST backend calls ``tree.root_node()``, ``node.kind()``,
    ``node.child_count()``, ``node.child(i)``, ``node.child_by_field_name()``,
    ``node.start_byte()/end_byte()`` — all as *methods*: the API of the official
    ``tree-sitter`` binding since its 0.25 rewrite. Pre-0.25 bindings expose
    these as *properties* (``.type``, no ``.kind``) — e.g. a sandbox's
    preinstalled older tree-sitter shadowing the bundled wheel — and would
    break the backend. ``_dispatch`` swallows per-file AST
    errors and falls back to regex, so without this one-time probe such a break
    would degrade every file silently."""
    try:
        root = parser.parse("class _ApiProbe {}").root_node()
        root.kind(); root.child_count(); root.start_byte(); root.end_byte()
        if root.child_count():
            kid = root.child(0)
            kid.kind(); kid.child_by_field_name("name")
        return True
    except Exception:
        return False


# --- pre-0.25 binding shim --------------------------------------------------- #
# A method-style facade over the property-style API of pre-0.25 bindings
# (``.type``/``.start_byte`` properties, ``parse(bytes)`` only) — e.g. a
# sandbox's preinstalled older tree-sitter shadowing the bundled wheel. The
# backend then runs unchanged against either binding generation.

class _NodeShim:
    __slots__ = ("_n",)

    def __init__(self, n):
        self._n = n

    def kind(self):
        return self._n.type

    def child_count(self):
        return self._n.child_count

    def child(self, i):
        c = self._n.children[i]
        return None if c is None else _NodeShim(c)

    def child_by_field_name(self, name):
        c = self._n.child_by_field_name(name)
        return None if c is None else _NodeShim(c)

    def start_byte(self):
        return self._n.start_byte

    def end_byte(self):
        return self._n.end_byte


class _TreeShim:
    __slots__ = ("_root",)

    def __init__(self, root):
        self._root = root

    def root_node(self):
        return _NodeShim(self._root)


class _ParserShim:
    __slots__ = ("_p",)

    def __init__(self, p):
        self._p = p

    def parse(self, src):
        if isinstance(src, str):
            src = src.encode("utf-8")
        return _TreeShim(self._p.parse(src).root_node)


def _adapt_property_api(parser):
    """Wrap a property-style parser in the method-style shim — or ``None`` if
    even the wrapped form fails the probe (an unknown third API shape)."""
    shim = _ParserShim(parser)
    return shim if _ast_api_supported(shim) else None


if _APEX_PARSER is not None and not _ast_api_supported(_APEX_PARSER):
    _APEX_PARSER = _adapt_property_api(_APEX_PARSER)
    if _APEX_PARSER is None:
        import logging

        logging.getLogger(__name__).warning(
            "Apex AST backend disabled: tree-sitter node API matches neither the "
            "0.25+ method style nor the pre-0.25 property style; using the regex "
            "backend.")


class ApexExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(".cls")

    def extract(self, path: Path):
        """Dispatch to the AST backend when the apex grammar is available, else
        the regex backend, then add custom-label edges. Never raises: a backend
        failure on odd input falls through to the other backend (AST -> regex) or
        to empty."""
        nodes, edges = self._dispatch(path)
        try:                                   # labels are never fatal
            cid = next((n["id"] for n in nodes if n.get("type") == "apexclass"), None)
            if cid:
                self._append_label_edges(path, cid, edges)
        except Exception:
            pass
        return nodes, edges

    def _dispatch(self, path: Path):
        # Gate on the module-level ``_APEX_PARSER`` so monkeypatching it to None
        # deterministically forces the regex fallback.
        if _APEX_PARSER is not None:
            try:
                return self._extract_ast(path)
            except Exception:
                # AST backend must never be worse than no extractor: fall back.
                pass
        try:
            return self._extract_regex(path)
        except Exception:
            return [], []

    def _append_label_edges(self, path: Path, cid: str, edges: list):
        """Append a `uses`->label edge per distinct custom label the class names.

        Comments/strings are stripped first so commented-out refs don't count.
        The label resolver turns `label/<name>` into a node (external stub if the
        label isn't in the repo)."""
        try:
            src = _strip_apex(path.read_text("utf-8", errors="replace"))
        except Exception:
            return
        for name in sorted(set(_LABEL_REF.findall(src))):
            if name:
                edges.append(raw_edge(cid, "uses", "label", name))

    # Backend dispatch wrappers. The heavy lifting lives in the `_regex` and
    # `_ast` submodules; these read the module-level `_APEX_PARSER` so dispatch
    # and the AST backend always agree on the live parser under monkeypatching.
    def _extract_regex(self, path: Path):
        return _extract_regex_impl(path)

    def _extract_ast(self, path: Path):
        return _extract_ast_impl(_APEX_PARSER, path)


EXTRACTORS = [ApexExtractor()]
