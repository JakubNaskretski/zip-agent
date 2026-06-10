"""Extracts org groupings — queues, public groups, and roles — which share one
extractor because they're the same shape: a named container with members.

  - a queue (`*.queue-meta.xml`) can own one or more sObject types
    (`<queueSobject><sobjectType>`); each is an `on` edge to the object,
  - a public group (`*.group-meta.xml`) is a flat bag of members,
  - a role (`*.role-meta.xml`) sits in the role hierarchy; its `<parentRole>`
    yields a `contains` edge from the parent to this role, plus a `parent` attr.

A member that maps to a node kind (nested `groups`, `roles`/`roleAndSubordinates`,
`queues`) becomes a `contains` edge; `users` (no node kind) stay attr-only. All
member developerNames are also kept in a flat `members` attr. Names and structure
only — labels, emails, and value text are never read.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..salesforce import _text
from ..xmlutil import iter_local as _iter_local

# Member container tags whose child developerNames become the `members` attr.
# Public groups and queues both nest membership under <members>.
_MEMBER_TAGS = ("groups", "users", "roles", "roleAndSubordinates",
                "roleAndSubordinatesInternal", "queues", "permissionSets")

# Member tag -> node kind, for the `contains` edges. `users` / `permissionSets`
# have no target node kind, so they stay attr-only.
_MEMBER_KIND = {
    "groups": "publicgroup",
    "roles": "role",
    "roleAndSubordinates": "role",
    "roleAndSubordinatesInternal": "role",
    "queues": "queue",
}


def _members(root):
    """Yield (member_tag, developerName) for every member nested under a
    <members> block. Names only — never labels or any value text."""
    seen: set[tuple[str, str]] = set()
    for members in _iter_local(root, "members"):
        for tag in _MEMBER_TAGS:
            for el in _iter_local(members, tag):
                name = (el.text or "").strip()
                if name and (tag, name) not in seen:
                    seen.add((tag, name))
                    yield tag, name


def _member_names(root) -> list[str]:
    """Flat list of member developerNames (order-preserving, de-duplicated)."""
    out: list[str] = []
    seen: set[str] = set()
    for _tag, name in _members(root):
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


class GroupingExtractor:
    source = "salesforce"

    _SUFFIXES = {
        ".queue-meta.xml": ("queue", "Queue"),
        ".group-meta.xml": ("publicgroup", "Public Group"),
        ".role-meta.xml": ("role", "Role"),
    }

    def handles(self, path: Path) -> bool:
        name = path.name
        return any(name.endswith(suffix) for suffix in self._SUFFIXES)

    def _kind_for(self, path: Path) -> tuple[str, str, str] | None:
        """(node_kind, suffix, name) for the file, or None if not ours."""
        for suffix, (kind, _label) in self._SUFFIXES.items():
            if path.name.endswith(suffix):
                return kind, suffix, path.name[: -len(suffix)]
        return None

    def extract(self, path: Path):
        info = self._kind_for(path)
        if info is None:
            return [], []
        kind, suffix, name = info

        try:
            root = ET.parse(path).getroot()
        except Exception:
            # Broken XML — emit the bare node from the filename.
            return [node(f"{kind}/{name}", kind, name)], []

        nid = f"{kind}/{name}"
        attrs: dict = {}
        edges = []

        members = _member_names(root)
        if members:
            attrs["members"] = members
        # membership -> `contains` edges to the member node (roles / nested
        # groups / queues). Users have no node kind, so they remain attr-only.
        for tag, mname in _members(root):
            mkind = _MEMBER_KIND.get(tag)
            if mkind:
                edges.append(raw_edge(nid, "contains", mkind, mname))

        if kind == "queue":
            # A Queue can own one or more sObject types -> `on` edge to each object.
            sobjects: list[str] = []
            seen: set[str] = set()
            for qs in _iter_local(root, "queueSobject"):
                sobj = (_text(qs, "sobjectType") or "").strip()
                if sobj and sobj not in seen:
                    seen.add(sobj)
                    sobjects.append(sobj)
                    edges.append(raw_edge(nid, "on", "object", sobj))
            if sobjects:
                attrs["sobjects"] = sobjects

        elif kind == "role":
            # Role hierarchy: parent <parentRole> -> `contains` -> this role.
            parent = (_text(root, "parentRole") or "").strip()
            if parent:
                attrs["parent"] = parent
                edges.append(raw_edge(f"role/{parent}", "contains", "role", name))

        return [node(nid, kind, name, **attrs)], edges


EXTRACTORS = [GroupingExtractor()]
