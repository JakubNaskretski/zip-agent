"""Visualforce extractor — `*.page` and `*.component` markup.

Emits:
  - node `vfpage/<Name>` for pages, `vfcomponent/<Name>` for components
    (Name from the filename stem),
  - `standardController="Object"` attr -> `references` -> object,
  - `controller`/`extensions` attrs -> `calls` -> apexclass (a comma list is
    split into one edge per class),
  - `<c:Comp>` custom-component tags -> `uses-component` -> vfcomponent.

Confidentiality: only tag/attribute structure is parsed — element names and a
small allow-list of structural attributes (standardController, controller,
extensions) plus the namespace of custom-component tags. No body/label/URL text
and no other attribute values leave here.

Visualforce markup is frequently not well-formed XML (HTML fragments, unclosed
tags, merge fields, inline script), so rather than an XML parser this scans tag
openers with a tolerant regex over the raw source and pulls attributes from each
opener's attribute span — never from element bodies.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..core import node, raw_edge

# A tag opener: `<` + optional namespace + tag name, then everything up to the
# matching `>` (its attribute span). Tags are not balanced or closed — only the
# opener's own attribute text is examined. `[^<>]*` keeps a single opener from
# swallowing across other tags/body, and the leading `(?![/!?])` skips end tags
# (`</x>`), comments and declarations.
_TAG = re.compile(r"<(?![/!?])\s*([A-Za-z_][\w-]*)(?::([A-Za-z_][\w-]*))?([^<>]*)>", re.S)

# An attribute inside a tag opener: name="value" | name='value' | name=value.
_ATTR = re.compile(
    r"""([A-Za-z_][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'<>`]+))""",
    re.S,
)

# The Visualforce root tag for each unit type (namespace `apex`):
#   <apex:page ...>      -> a page's controller/standardController/extensions
#   <apex:component ...> -> a component's controller (no standardController)
_ROOT_TAGS = {"page", "component"}


def _attrs(span: str) -> dict[str, str]:
    """Parse the attribute span of one tag opener into {name_lower: value}.

    Names are collected case-insensitively. Values are returned verbatim so the
    caller can split the controller/extension class names and the
    standardController object name; only those structural references are emitted.
    """
    out: dict[str, str] = {}
    for m in _ATTR.finditer(span):
        name = m.group(1).lower()
        value = m.group(2) or m.group(3) or m.group(4) or ""
        out.setdefault(name, value)
    return out


def _split_classes(value: str) -> list[str]:
    """`"NsExt1, Acme.Ext2 "` -> ["NsExt1", "Ext2"]: split a comma list and take
    the last dotted segment of each (drops a leading namespace), de-duped/ordered."""
    out: list[str] = []
    seen: set[str] = set()
    for part in value.split(","):
        cls = part.strip()
        if not cls:
            continue
        cls = cls.split(".")[-1].strip()      # drop namespace prefix if any
        if cls and cls not in seen:
            seen.add(cls)
            out.append(cls)
    return out


class VisualforceExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        name = path.name
        # Own the markup files only — not the `*-meta.xml` config sidecars.
        return name.endswith(".page") or name.endswith(".component")

    def extract(self, path: Path):
        is_page = path.name.endswith(".page")
        kind = "vfpage" if is_page else "vfcomponent"
        name = path.stem                     # `Foo.page` -> `Foo`
        nid = f"{kind}/{name}"
        nodes = [node(nid, kind, name)]
        edges: list[dict] = []

        try:
            src = path.read_text("utf-8", errors="ignore")
        except Exception:
            return nodes, edges              # unreadable file -> bare node, no edges

        seen_class: set[str] = set()         # apexclass calls already emitted
        seen_comp: set[str] = set()          # vfcomponent uses already emitted
        seen_object = False                  # at most one references->object (root)

        try:
            for m in _TAG.finditer(src):
                # `<ns:local ...>`: group(1) is the first name part (the namespace
                # prefix when a colon follows, else the bare tag), group(2) is the
                # local name after the colon (None when the tag is un-namespaced),
                # group(3) is the attribute span.
                prefix = (m.group(1) or "")
                local = m.group(2)                  # None when there's no colon
                ns = prefix.lower() if local else ""
                tag = (local or prefix).lower()
                span = m.group(3) or ""

                # Custom component tag `<c:Comp ...>` -> uses-component -> vfcomponent.
                if ns == "c" and local:
                    comp = local
                    if comp not in seen_comp:
                        seen_comp.add(comp)
                        edges.append(raw_edge(nid, "uses-component", "vfcomponent", comp))
                    continue

                # Root `<apex:page>` / `<apex:component>` -> structural attrs.
                if ns == "apex" and tag in _ROOT_TAGS:
                    a = _attrs(span)

                    # standardController -> references -> object (pages only).
                    sc = a.get("standardcontroller", "").strip()
                    if sc and not seen_object:
                        seen_object = True
                        edges.append(raw_edge(nid, "references", "object", sc))

                    # controller + extensions -> calls -> apexclass (split list).
                    for key in ("controller", "extensions"):
                        for cls in _split_classes(a.get(key, "")):
                            if cls not in seen_class:
                                seen_class.add(cls)
                                edges.append(raw_edge(nid, "calls", "apexclass", cls))
        except Exception:
            # Any parsing oddity: keep whatever edges we already formed, skip the rest.
            return nodes, edges

        return nodes, edges


EXTRACTORS = [VisualforceExtractor()]
