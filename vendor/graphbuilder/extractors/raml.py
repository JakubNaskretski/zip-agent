"""RAML API-spec extractor (``src/main/resources/**/*.raml``).

A root RAML document (first line ``#%RAML 0.8|1.0``, no fragment word) becomes an
``apispec`` node — keyed by its FILE NAME (``apispec/orders.raml``), the same
form an ``<apikit:config raml=…/api=…>`` names it by (see
``mulesoft.spec_name``), so the config's ``boundto`` edge resolves without a
custom resolver. Each resource the spec declares becomes an ``apiresource`` node
(keyed by its full path, ``apiresource//orders/{orderId}``) plus a ``declares``
edge; the HTTP methods found under a resource land in its ``methods`` attr.
APIkit-named flows ``implements``-edge to the same path-keyed ids (see
``extractors/mule.py``), which is what joins spec to implementation.

RAML is YAML, and the engine is stdlib-only — so this is a deliberately small
indent-stack scanner, not a YAML parser: resource keys are lines matching
``/path:``, nested resources concatenate onto their parent's path, methods are
``get:``/``post:``/… keys under a resource, and block scalars (``|``/``>``) are
skipped so documentation text can't fake a resource. Fragments
(``#%RAML 1.0 Trait`` etc.) and non-RAML files yield nothing. Never raises on
content (an unreadable file surfaces through the build's ``errors`` channel).
"""
from __future__ import annotations

import re
from pathlib import Path

from ..core import node, raw_edge
from ..mulesoft import HTTP_METHODS, is_resources_path, resource_rel_path

# A root RAML header: `#%RAML 1.0` / `#%RAML 0.8` with nothing after the version
# (a trailing word — Trait, DataType, Library, … — marks a fragment, not an API).
_ROOT_RE = re.compile(r"#%RAML\s+\d+\.\d+\s*$")
_TITLE_RE = re.compile(r"^title:\s*(.+?)\s*$", re.M)
_RESOURCE_RE = re.compile(r"^(\s*)(/[^:\s][^:]*|/):\s*(#.*)?$")
_METHOD_RE = re.compile(r"^(\s*)(%s)\??:" % "|".join(HTTP_METHODS), re.I)
_BLOCK_RE = re.compile(r":\s*[|>][+-]?\s*(#.*)?$")   # `key: |` / `key: >` block scalar


def parse_raml(text: str) -> dict | None:
    """``{"title", "resources": {path: sorted-method-list}}`` for a root RAML
    document, or ``None`` for a fragment / non-RAML text."""
    lines = text.splitlines()
    if not lines or not _ROOT_RE.match(lines[0].strip()):
        return None
    title_m = _TITLE_RE.search(text)
    resources: dict[str, list] = {}
    methods: dict[str, set] = {}
    # open resources: [indent, full path, direct-child indent (-1 until seen)]
    stack: list[list] = []
    block_indent = -1                          # inside a `|`/`>` scalar when >= 0
    for line in lines[1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if block_indent >= 0:
            if indent > block_indent:
                continue                       # literal block content — not keys
            block_indent = -1
        # ANY key at or left of the innermost open resource closes it — so a
        # later top-level section (`types:`, `traits:` …) can't leave a resource
        # open and adopt its look-alike keys (a type property named `get:`).
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if stack and stack[-1][2] < 0:         # first line inside = direct-child level
            stack[-1][2] = indent
        r = _RESOURCE_RE.match(line)
        if r:
            full = (stack[-1][1] if stack else "") + r.group(2)
            stack.append([indent, full, -1])
            resources.setdefault(full, [])
            continue
        m = _METHOD_RE.match(line)
        # a method is a DIRECT child of the innermost resource — deeper matches
        # (a query parameter named `delete:` …) are that child's content, not API
        if m and stack and indent == stack[-1][2]:
            methods.setdefault(stack[-1][1], set()).add(m.group(2).lower())
        if _BLOCK_RE.search(line):
            block_indent = indent
    for path, ms in methods.items():
        resources[path] = sorted(ms)
    return {"title": title_m.group(1) if title_m else "", "resources": resources}


class RamlExtractor:
    source = "mule"

    def handles(self, path: Path) -> bool:
        return path.suffix == ".raml" and is_resources_path(path)

    def extract(self, path: Path):
        spec = parse_raml(path.read_text("utf-8", errors="replace"))
        if spec is None:
            return [], []
        rel = resource_rel_path(path)
        sid = f"apispec/{path.name}"
        nodes = [node(sid, "apispec", path.name, file=rel, title=spec["title"])]
        edges = []
        for rpath, methods in sorted(spec["resources"].items()):
            nodes.append(node(f"apiresource/{rpath}", "apiresource", rpath,
                              methods=methods, spec=path.name))
            edges.append(raw_edge(sid, "declares", "apiresource", rpath))
        return nodes, edges


EXTRACTORS = [RamlExtractor()]
