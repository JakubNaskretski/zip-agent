"""Lightning Web Component bundles.

Owns each bundle's main module `*/lwc/<name>/<name>.js`. Emits one `lwc` node per
bundle plus edges: `calls` (->apexclass), `uses-component` (->lwc), `aura-enabled`
(->apexmethod/<Class>.<method>), `wire` (->object, ->field, ->apexmethod), and
`uses` (->label/resource/messagechannel).

Composition is read from the bundle's `.html` template(s): child custom elements
`<c-some-cmp>` become `uses-component` edges (kebab tag -> camelCase bundle name).
Wired Apex (`@wire(getStuff, ...)` where `getStuff` is imported from
`@salesforce/apex/Class.method`) becomes a `wire` edge to `apexmethod/Class.method`,
alongside the import's own `aura-enabled` edge.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..core import node, raw_edge
from ..salesforce import parse_lwc

# import specifiers we scan for in the bundle's main module
_APEX_METHOD = re.compile(r"""['"]@salesforce/apex/(\w+)\.(\w+)['"]""")
_SCHEMA = re.compile(r"""['"]@salesforce/schema/([\w.$]+)['"]""")
_LABEL = re.compile(r"""['"]@salesforce/label/([\w.$]+)['"]""")
_RESOURCE = re.compile(r"""['"]@salesforce/resourceUrl/([\w.$]+)['"]""")
_MESSAGE_CHANNEL = re.compile(r"""['"]@salesforce/messageChannel/([\w.$]+)['"]""")

# `import <localName> from '@salesforce/apex/<Class>.<method>'` — maps the local
# binding to the apex method it points at, so a @wire on that binding resolves.
_APEX_IMPORT_BINDING = re.compile(
    r"""import\s+(\w+)\s+from\s+['"]@salesforce/apex/(\w+)\.(\w+)['"]"""
)
# `@wire(<adapter>, ...)` — the adapter is the first argument to the decorator.
_WIRE_ADAPTER = re.compile(r"@wire\s*\(\s*(\w+)")

# custom child element in a template: <c-some-cmp ...> (kebab-case, c- namespace).
_CUSTOM_ELEMENT = re.compile(r"<c-([a-z0-9]+(?:-[a-z0-9]+)*)\b")

# A managed-package child element: <ns-comp-name ...> where `ns` is the package
# namespace (may contain underscores, e.g. acme_pkg). Platform namespaces are
# skipped; the local `c` namespace is handled by _CUSTOM_ELEMENT above.
_NAMESPACED_ELEMENT = re.compile(r"<([a-z][a-z0-9_]*)-([a-z0-9_]+(?:-[a-z0-9_]+)*)\b")
_BUILTIN_TAG_NS = frozenset({
    "c", "lightning", "lwc", "aura", "ui", "force", "forcechatter",
    "forcecommunity", "ltng", "laf", "lightningsnapin", "site", "clients",
})


def _kebab_to_camel(tag: str) -> str:
    """`acme-reading-card` -> `acmeReadingCard` (the LWC bundle/folder name)."""
    parts = [p for p in tag.split("-") if p]
    if not parts:
        return ""
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


class LwcExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        try:
            return (
                path.suffix == ".js"
                and path.stem == path.parent.name
                and path.parent.parent.name == "lwc"
            )
        except Exception:
            return False

    def extract(self, path: Path):
        bundle = parse_lwc(path.parent)
        lid = f"lwc/{bundle.name}"
        nodes = [node(lid, "lwc", bundle.name)]
        edges = []

        # base: Apex controllers and composed components (from parse_lwc)
        for cls in sorted(bundle.class_refs):
            if cls:
                edges.append(raw_edge(lid, "calls", "apexclass", cls))
        # composed components come from JS imports (parse_lwc) AND the template(s);
        # dedupe so a component used in both places yields one uses-component edge.
        composed = set(bundle.lwc_refs)
        composed |= self._template_components(path.parent, bundle.name)
        for comp in sorted(composed):
            if comp:
                edges.append(raw_edge(lid, "uses-component", "lwc", comp))

        # deep: scan the main module source for @salesforce/* specifiers
        src = bundle.source or ""

        # local binding name -> "<Class>.<method>" for each apex import (for @wire)
        apex_bindings = {
            local: f"{cls}.{method}"
            for local, cls, method in _APEX_IMPORT_BINDING.findall(src)
            if local and cls and method
        }

        # @salesforce/apex/<Class>.<method> -> aura-enabled apexmethod
        for cls, method in _APEX_METHOD.findall(src):
            if cls and method:
                edges.append(raw_edge(lid, "aura-enabled", "apexmethod", f"{cls}.{method}"))

        # @wire(<adapter>, ...): if the adapter is an imported apex method binding,
        # emit a wire edge straight to that apexmethod (the aura-enabled import edge
        # above is kept independently).
        for adapter in _WIRE_ADAPTER.findall(src):
            target = apex_bindings.get(adapter)
            if target:
                edges.append(raw_edge(lid, "wire", "apexmethod", target))

        # @salesforce/schema/<Object> or <Object>.<Field> -> wire
        for ref in _SCHEMA.findall(src):
            obj, _, fld = ref.partition(".")
            if not obj:
                continue
            if fld:
                edges.append(raw_edge(lid, "wire", "field", f"{obj}.{fld}"))
            else:
                edges.append(raw_edge(lid, "wire", "object", obj))

        # labels / static resources / message channels -> uses
        for name in _LABEL.findall(src):
            if name:
                edges.append(raw_edge(lid, "uses", "label", name))
        for name in _RESOURCE.findall(src):
            if name:
                edges.append(raw_edge(lid, "uses", "resource", name))
        for name in _MESSAGE_CHANNEL.findall(src):
            if name:
                edges.append(raw_edge(lid, "uses", "messagechannel", name))

        return nodes, edges

    @staticmethod
    def _template_components(bundle_dir: Path, self_name: str) -> set:
        """Custom child components referenced in the bundle's `.html` template(s).

        `<c-acme-reading-card>` -> `acmeReadingCard`. An unreadable or malformed
        template contributes nothing.
        """
        found: set = set()
        try:
            templates = sorted(bundle_dir.glob("*.html"))
        except Exception:
            return found
        for tpl in templates:
            try:
                html = tpl.read_text("utf-8", errors="replace")
            except Exception:
                continue
            for tag in _CUSTOM_ELEMENT.findall(html):
                name = _kebab_to_camel(tag)
                if name and name != self_name:
                    found.add(name)
            # Managed-package children keep their namespace:
            # <acme_pkg-card-frame> -> acme_pkg__cardFrame (matching how flows
            # reference namespaced LWC).
            for ns, tag in _NAMESPACED_ELEMENT.findall(html):
                if ns in _BUILTIN_TAG_NS:
                    continue
                name = _kebab_to_camel(tag)
                if name:
                    found.add(f"{ns}__{name}")
        return found


EXTRACTORS = [LwcExtractor()]
