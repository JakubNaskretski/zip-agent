"""Mule property-file extractor (``src/main/resources/**/*.{properties,yaml,yml}``).

Each file becomes a ``propertyfile`` node keyed by its path under
``src/main/resources`` — the exact form a ``<configuration-properties file=…>``
attribute names it by, so the config extractor's ``loads`` edge resolves to it
without a custom resolver. Every property KEY defined in the file becomes a
``propertykey`` node + a ``defineskey`` edge.

**Property VALUES are never captured** — they are where hosts, ports, user names
and (mis-placed) secrets live. Keys only, per the engine's names-only rule; the
``${key}`` *reads* side is wired by the config extractor.

Scope heuristic: properties/YAML under ``src/main/resources``, excluding
anything under an ``api`` segment (that subtree belongs to the API spec — RAML
examples and OAS documents are YAML too, but they are not property files).

Parsing is stdlib-only and never raises on content:
  - ``.properties`` — ``key=value`` / ``key: value`` lines; ``#``/``!`` comments;
    a trailing ``\\`` continues the VALUE onto the next line (skipped).
  - ``.yaml`` / ``.yml`` — a small indent-stack flattener (``db: {host: …}`` ->
    ``db.host``), leaf keys only, list items and block scalars skipped. Mule's
    property files are flat key/scalar maps; anything fancier (anchors, multi-
    document) simply yields fewer keys, never an error.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..core import node, raw_edge
from ..mulesoft import is_resources_path, resource_rel_path

_SUFFIXES = (".properties", ".yaml", ".yml")
_YAML_KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_$][A-Za-z0-9_.$-]*|\"[^\"]+\"|'[^']+'):(.*)$")
_BLOCK_VALUE_RE = re.compile(r"^\s*[|>][+-]?\s*(#.*)?$")


def parse_properties(text: str) -> list:
    """Sorted property keys of a ``.properties`` text (keys only, no values)."""
    keys: set = set()
    continued = False
    for line in text.splitlines():
        if continued:                       # previous line's VALUE continues here
            continued = line.rstrip().endswith("\\")
            continue
        stripped = line.strip()
        if not stripped or stripped[0] in "#!":
            continue
        continued = line.rstrip().endswith("\\")
        # the key ends at the FIRST `=` or `:` (the .properties separator rule)
        cuts = [i for i in (stripped.find("="), stripped.find(":")) if i >= 0]
        if not cuts:
            continue
        key = stripped[:min(cuts)].strip()
        if key:
            keys.add(key)
    return sorted(keys)


def parse_yaml_keys(text: str) -> list:
    """Sorted dotted leaf keys of a (flat, Mule-style) YAML mapping —
    ``db:\\n  host: x`` -> ``db.host``. Values are read past, never kept."""
    keys: set = set()
    stack: list[tuple[int, str]] = []       # (indent, dotted prefix) of open maps
    block_indent = -1
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if block_indent >= 0:
            if indent > block_indent:
                continue
            block_indent = -1
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if line.lstrip().startswith("- "):   # list items aren't property keys
            continue
        m = _YAML_KEY_RE.match(line)
        if not m:
            continue
        key = m.group(2).strip("\"'")
        rest = m.group(3).strip()
        dotted = ".".join([p for _, p in stack] + [key]) if stack else key
        if rest and not rest.startswith("#"):
            if _BLOCK_VALUE_RE.match(rest):  # `key: |` — value spans lines below
                block_indent = indent
            keys.add(dotted)                 # leaf with an inline value
        else:
            stack.append((indent, key))      # (potential) nested map — not a leaf
    return sorted(keys)


class MulePropertiesExtractor:
    source = "mule"

    def handles(self, path: Path) -> bool:
        if path.suffix not in _SUFFIXES or not is_resources_path(path):
            return False
        return "api" not in Path(resource_rel_path(path)).parts[:-1]

    def extract(self, path: Path):
        text = path.read_text("utf-8", errors="replace")
        keys = (parse_properties(text) if path.suffix == ".properties"
                else parse_yaml_keys(text))
        rel = resource_rel_path(path)
        fid = f"propertyfile/{rel}"
        fmt = "properties" if path.suffix == ".properties" else "yaml"
        nodes = [node(fid, "propertyfile", rel, format=fmt)]
        edges = []
        for key in keys:
            nodes.append(node(f"propertykey/{key}", "propertykey", key))
            edges.append(raw_edge(fid, "defineskey", "propertykey", key))
        return nodes, edges


EXTRACTORS = [MulePropertiesExtractor()]
