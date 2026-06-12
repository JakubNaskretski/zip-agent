"""Extracts Aura component bundles.

Owns the bundle's markup file `*/aura/<Name>/<Name>.cmp` (and the application
`*.app` / event `*.evt` markup, which share the bundle shape and grammar). One
bundle becomes one `aura/<Name>` node with edges:
  - `<c:childCmp .../>` custom component tags  -> uses-component -> aura/<child>
  - `controller="ClassName"` bundle attribute  -> calls -> apexclass/<Class>
  - `force:recordData` object attrs and `aura:dependency` resources naming an
    object                                      -> references -> object/<Object>

The bundle's JavaScript controller/helper (`<Name>Controller.js` /
`<Name>Helper.js`), when present, is also scanned for Apex server actions
(`apex://Class`) and `$A.createComponent("c:childCmp", ...)` dynamic composition.

Names and structure only — no attribute values, labels, formulas, or URLs.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..core import node, raw_edge

# ---- markup-level patterns ------------------------------------------------- #

# A custom Aura component tag in the `c` namespace: `<c:childCmp ...>` or
# `<c:ChildCmp/>`. Captures the child bundle name only (no attributes/values).
_CUSTOM_TAG = re.compile(r"<c:([A-Za-z_]\w*)\b")

# Dynamic creation in JS: `$A.createComponent("c:childCmp", ...)` (single or
# double quotes); the markup-namespaced name is captured.
_CREATE_COMPONENT = re.compile(r"""createComponent\s*\(\s*['"]c:([A-Za-z_]\w*)['"]""")

# Managed-package component refs: `<ns:Comp>` in markup and
# `$A.createComponent("ns:Comp", ...)` in JS. Platform namespaces are skipped;
# the local `c` namespace is handled by the two patterns above.
_NS_TAG = re.compile(r"<([A-Za-z]\w*):([A-Za-z_]\w*)\b")
_NS_CREATE_COMPONENT = re.compile(
    r"""createComponent\s*\(\s*['"]([A-Za-z_]\w*):([A-Za-z_]\w*)['"]"""
)
_BUILTIN_AURA_NS = frozenset({
    "c", "aura", "ui", "lightning", "force", "forcechatter", "forcecommunity",
    "ltng", "design", "apex", "flexipage", "wave", "lightningsnapin",
})

# `controller="ClassName"` — the Apex server-side controller of the bundle. May be
# namespaced (`MyNs.Handler`); the class is the head's last segment.
_CONTROLLER_ATTR = re.compile(r"""\bcontroller\s*=\s*['"]([\w.]+)['"]""")

# `aura:dependency` whose `resource` names an object, e.g.
# `<aura:dependency resource="markup://c:Account" type="..."/>` or a bare object.
_DEPENDENCY_TAG = re.compile(r"<aura:dependency\b[^>]*>", re.S)
_RESOURCE_ATTR = re.compile(r"""\bresource\s*=\s*['"]([^'"]+)['"]""")

# A force:recordData tag (we read only its object-naming attributes).
_RECORD_DATA_TAG = re.compile(r"<force:recordData\b[^>]*>", re.S)

# Within a recordData / dependency tag, an explicit object name attribute.
# `targetObject`/`object`/`sobjectType`/`recordObject` = "Account" — names only.
_OBJECT_ATTR = re.compile(
    r"""\b(?:targetObject|sobjectType|recordObject|object|entityName)\s*=\s*['"]([\w.]+)['"]"""
)

# JS server action via `apex://Class` (used by Aura `enableServerSideController`
# patterns and Lightning Data Service helpers). Names only.
_APEX_URI = re.compile(r"""apex://([\w.]+)""")


def _looks_like_object(name: str) -> bool:
    """Heuristic for an object reference: a custom object of any suffix
    (`Foo__c`, `Foo__e`, `ns__Foo__mdt`, `Foo__x`, `Foo__b`) or a capitalized
    standard-object identifier (`Account`). Avoids field paths and lowercase view
    bindings; skips when in doubt. The suffix check matters for managed-package
    objects, whose lowercase namespace prefix fails the capitalization fallback."""
    if not name:
        return False
    if name.lower().endswith(("__c", "__e", "__mdt", "__x", "__b")):
        return True
    return name[:1].isupper()


class AuraExtractor:
    source = "salesforce"

    # bundle markup files owned here (the bundle is the parent dir under `aura/`)
    _MARKUP_SUFFIXES = (".cmp", ".app", ".evt")

    def handles(self, path: Path) -> bool:
        try:
            return (
                path.suffix in self._MARKUP_SUFFIXES
                and path.stem == path.parent.name
                and path.parent.parent.name == "aura"
            )
        except Exception:
            return False

    def extract(self, path: Path):
        name = path.stem
        aid = f"aura/{name}"
        nodes = [node(aid, "aura", name)]
        edges: list[dict] = []

        markup = _read(path)

        # ---- bundle JS (controller/helper) for server actions + dynamic cmps --
        js_src = self._bundle_js(path.parent, name)

        # ---- uses-component -> aura/<child> (markup tags + JS createComponent) -
        children: set[str] = set()
        for child in _CUSTOM_TAG.findall(markup):
            if child and child != name:
                children.add(child)
        for child in _CREATE_COMPONENT.findall(js_src):
            if child and child != name:
                children.add(child)
        # Managed-package children keep their namespace:
        # <acme_pkg:CardFrame> -> acme_pkg__CardFrame.
        for ns, child in _NS_TAG.findall(markup):
            if child and ns.lower() not in _BUILTIN_AURA_NS:
                children.add(f"{ns}__{child}")
        for ns, child in _NS_CREATE_COMPONENT.findall(js_src):
            if child and ns.lower() not in _BUILTIN_AURA_NS:
                children.add(f"{ns}__{child}")
        for child in sorted(children):
            edges.append(raw_edge(aid, "uses-component", "aura", child))

        # ---- calls -> apexclass/<Class> (controller attr + apex:// in JS) ------
        classes: set[str] = set()
        for cls in _CONTROLLER_ATTR.findall(markup):
            seg = cls.split(".")[-1]   # strip a leading namespace
            if seg:
                classes.add(seg)
        for cls in _APEX_URI.findall(js_src):
            seg = cls.split(".")[-1]
            if seg:
                classes.add(seg)
        for cls in sorted(classes):
            edges.append(raw_edge(aid, "calls", "apexclass", cls))

        # ---- references -> object/<Object> (force:recordData + aura:dependency) -
        objects: set[str] = set()
        for tag in _RECORD_DATA_TAG.findall(markup):
            for obj in _OBJECT_ATTR.findall(tag):
                seg = obj.split(".")[0]   # take the leading object segment only
                if _looks_like_object(seg):
                    objects.add(seg)
        for tag in _DEPENDENCY_TAG.findall(markup):
            for res in _RESOURCE_ATTR.findall(tag):
                obj = self._object_from_resource(res)
                if obj and _looks_like_object(obj):
                    objects.add(obj)
        for obj in sorted(objects):
            edges.append(raw_edge(aid, "references", "object", obj))

        return nodes, edges

    @staticmethod
    def _bundle_js(bundle_dir: Path, name: str) -> str:
        """Concatenated text of the bundle's controller/helper JS, if present.
        Never raises — an unreadable/missing file contributes the empty string."""
        out: list[str] = []
        for suffix in ("Controller.js", "Helper.js"):
            try:
                p = bundle_dir / f"{name}{suffix}"
                if p.is_file():
                    out.append(_read(p))
            except Exception:
                continue
        return "\n".join(out)

    @staticmethod
    def _object_from_resource(res: str) -> str:
        """Extract an object-ish name from an `aura:dependency` resource string,
        e.g. `markup://c:Account` -> `Account`, `apex://Account__c` -> `Account__c`,
        a bare `Account` -> `Account`. Returns "" when nothing object-shaped."""
        if not res:
            return ""
        tail = res.rsplit("://", 1)[-1]      # drop a `markup://` / `apex://` scheme
        if ":" in tail:                       # `c:Account` -> `Account`
            tail = tail.rsplit(":", 1)[-1]
        if "." in tail:                       # `c.Account` -> `Account`
            tail = tail.rsplit(".", 1)[-1]
        return tail.strip()


def _read(path: Path) -> str:
    try:
        return path.read_text("utf-8", errors="replace")
    except Exception:
        return ""


EXTRACTORS = [AuraExtractor()]
