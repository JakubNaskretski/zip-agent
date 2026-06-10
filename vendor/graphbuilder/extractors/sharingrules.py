"""Sharing-rules extractor — `*.sharingRules-meta.xml`.

One file per object, `sharingRules/<Object>.sharingRules-meta.xml`, containing
`sharingCriteriaRules` / `sharingOwnerRules` / `sharingGuestRules`. The
`<Object>` is taken from the filename (it is the object the rules govern).

Each rule becomes a `sharingrule` node id `sharingrule/<Object>.<fullName>` with
a `rule_type` attr (`criteria` | `owner` | `guest`), and:
  - `on` -> object  (the governed object, from the filename),
  - `reads` -> field  (each `<field>` named in a `<criteriaItems>` filter; both
    criteria and guest rules can carry these),
  - `references` -> role | publicgroup  (the `<sharedTo>` / `<sharedFrom>`
    principal, when it is a role or public group).

Confidentiality: names and structure only. The share-to target developerName is
also kept as a node attr `shared_to`. Territory targets have no node kind, so
they stay attr-only. Criterion `<value>` / `<operation>` and any label/formula
text are never emitted — only the criterion `<field>` name.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..salesforce import _text
from ..xmlutil import child as _child, iter_local as _iter_local

# rule wrapper tag -> rule_type attr value
_RULE_TAGS = {
    "sharingCriteriaRules": "criteria",
    "sharingOwnerRules": "owner",
    "sharingGuestRules": "guest",
}

# developerName-bearing children of <sharedTo>/<sharedFrom>, mapped to the graph
# principal node kind. Territories have no node kind -> kept only as the attr.
_SHARE_PRINCIPAL_KIND = {
    "group": "publicgroup",
    "role": "role",
    "roleAndSubordinates": "role",
    "roleAndSubordinatesInternal": "role",
}
_SHARE_TARGET_TAGS = ("group", "role", "roleAndSubordinates",
                      "roleAndSubordinatesInternal", "territory",
                      "territoryAndSubordinates")


def _principal(container):
    """(node_kind, name) for the first principal child under a <sharedTo>/
    <sharedFrom>, or None. Territories (no node kind) and constants like
    <allInternalUsers/> yield None."""
    if container is None:
        return None
    for tag in _SHARE_TARGET_TAGS:
        val = _text(container, tag)
        if val:
            kind = _SHARE_PRINCIPAL_KIND.get(tag)
            return (kind, val) if kind else None
    return None


def _shared_to(rule) -> str:
    """The share-to target developerName (a group/role/territory name), if any —
    kept as a plain name attr. Returns "" when the target is a constant such as
    <allInternalUsers/> (no developerName to record)."""
    shared = _child(rule, "sharedTo")
    if shared is None:
        return ""
    for tag in _SHARE_TARGET_TAGS:
        val = _text(shared, tag)
        if val:
            return val
    return ""


def _criteria_fields(rule):
    """Field names referenced by a criteria rule's <criteriaItems>.

    Only the <field> name is taken; <operation>/<value> are values and never
    enter the graph."""
    fields = []
    seen = set()
    for ci in _iter_local(rule, "criteriaItems"):
        f = _text(ci, "field")
        if f and f not in seen:
            seen.add(f)
            fields.append(f)
    return fields


class SharingRulesExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(".sharingRules-meta.xml")

    def extract(self, path: Path):
        # object is the filename stem before the suffix
        obj = path.name[: -len(".sharingRules-meta.xml")]
        nodes: list[dict] = []
        edges: list[dict] = []
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            return nodes, edges

        for wrapper, rule_type in _RULE_TAGS.items():
            for rule in _iter_local(root, wrapper):
                full = _text(rule, "fullName")
                if not full:
                    continue
                rid = f"sharingrule/{obj}.{full}"
                attrs = {"rule_type": rule_type}
                shared_to = _shared_to(rule)
                if shared_to:
                    attrs["shared_to"] = shared_to
                nodes.append(node(rid, "sharingrule", full, **attrs))

                # governs the object named in the filename
                if obj:
                    edges.append(raw_edge(rid, "on", "object", obj))

                # share-to / share-from principals -> references -> role/publicgroup
                for tag in ("sharedTo", "sharedFrom"):
                    principal = _principal(_child(rule, tag))
                    if principal:
                        kind, pname = principal
                        edges.append(raw_edge(rid, "references", kind, pname))

                # field refs in the filter -> reads -> field. Any rule type may
                # carry <criteriaItems> (criteria rules always do; guest rules
                # commonly do too), so we read them off whatever rule has them.
                for f in _criteria_fields(rule):
                    # SF emits criterion fields as "<Object>.<Field>"; if a bare
                    # field name slips through, qualify it with the governed
                    # object so it matches field/<Object>.<Field>.
                    qual = f if "." in f else (f"{obj}.{f}" if obj else f)
                    edges.append(raw_edge(rid, "reads", "field", qual))

        return nodes, edges


EXTRACTORS = [SharingRulesExtractor()]
