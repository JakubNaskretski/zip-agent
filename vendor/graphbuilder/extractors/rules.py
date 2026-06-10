"""Record-action rules extractor — assignment / escalation / duplicate / matching.

Covers the four record-action rule types, not Workflow Rules
(`*.workflow-meta.xml`). Each contained rule becomes one node:

    assignmentrule | escalationrule | duplicaterule | matchingrule

with id ``<ruletype>/<Object-or-fullName>.<ruleName>``. Edges:
  - `on` -> object  : the governed object (from the filename, or `<sobjectType>`
    on a duplicate rule),
  - `reads` -> field : each criterion / matching field name referenced, qualified
    as `<Object>.<Field>` so it matches `field/<Object>.<Field>`.

File layouts:
  - `*.assignmentRules-meta.xml`  (root <AssignmentRules>): N `<assignmentRule>`,
    each with `<fullName>` and `<ruleEntries>`/`<criteriaItems>`. Object = filename.
  - `*.escalationRules-meta.xml`  (root <EscalationRules>): N `<escalationRule>`,
    same entry/criteria shape. Object = filename (commonly Case).
  - `*.duplicateRule-meta.xml`    (root <DuplicateRule>): ONE rule. fullName is
    `<Object>.<RuleName>`; object also in `<duplicateRuleMatchRules>/<matchRuleSObjectType>`.
    Field refs come from `<duplicateRuleFilterItems>/<field>`.
  - `*.matchingRule-meta.xml`     (root <MatchingRules>): N `<matchingRules>`, each
    with `<fullName>` and `<matchingRuleItems>/<fieldName>`. Object = filename.

Confidentiality: names and structure only. A criterion is reduced to its
`<field>`/`<fieldName>` name — operation, value, label/subject/body, formulas
and assignee text are never emitted.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..salesforce import _text
from ..xmlutil import iter_local as _iter_local

# suffix -> (node type, container tag, file kind)
#   container tag is the per-rule wrapper for the multi-rule files; None means the
#   file itself is a single rule (duplicateRule).
_ASSIGNMENT = ".assignmentRules-meta.xml"
_ESCALATION = ".escalationRules-meta.xml"
_DUPLICATE = ".duplicateRule-meta.xml"
_MATCHING = ".matchingRule-meta.xml"

_SUFFIXES = (_ASSIGNMENT, _ESCALATION, _DUPLICATE, _MATCHING)


def _qual_field(name: str, obj: str) -> str:
    """Qualify a criterion field to `<Object>.<Field>`.

    Criterion fields usually arrive already qualified (e.g. `Lead.Industry`); a
    bare `Industry` is qualified with the governed object so it matches the
    `field/<Object>.<Field>` node id."""
    if not name:
        return ""
    if "." in name:
        return name
    return f"{obj}.{name}" if obj else name


def _criteria_fields(rule):
    """Field names referenced under any `<criteriaItems>` of a rule (assignment /
    escalation). Only `<field>` is taken; `<operation>`/`<value>` are values and
    never enter the graph."""
    out = []
    seen = set()
    for ci in _iter_local(rule, "criteriaItems"):
        f = _text(ci, "field")
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _matching_fields(rule):
    """Field names referenced under a matching rule's `<matchingRuleItems>`
    (`<fieldName>`). Blank-handling/method are ignored."""
    out = []
    seen = set()
    for it in _iter_local(rule, "matchingRuleItems"):
        f = _text(it, "fieldName")
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _duplicate_fields(rule):
    """Field names referenced by a duplicate rule's filter (`<field>` under
    `<duplicateRuleFilterItems>`). Values/operations are never emitted."""
    out = []
    seen = set()
    for it in _iter_local(rule, "duplicateRuleFilterItems"):
        f = _text(it, "field")
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _action_edges(rule, rid, obj, edges):
    """Routing edges for assignment / escalation rules:
      - assignee Queue (`<assignedToType>Queue` + `<assignedTo>`) -> `references`
        -> queue (found at ruleEntries level and inside `<escalationAction>`),
      - notification `<template>` -> `uses` -> emailtemplate.
    User assignees have no node kind and are skipped."""
    queues, templates = set(), set()
    for container in rule.iter():
        if _text(container, "assignedToType") == "Queue":
            q = _text(container, "assignedTo")
            if q:
                queues.add(q)
    for t in _iter_local(rule, "template"):
        name = (t.text or "").strip()
        if name:
            templates.add(name)
    for q in sorted(queues):
        edges.append(raw_edge(rid, "references", "queue", q))
    for tmpl in sorted(templates):
        edges.append(raw_edge(rid, "uses", "emailtemplate", tmpl))


class RuleExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(_SUFFIXES)

    def extract(self, path: Path):
        name = path.name
        if name.endswith(_ASSIGNMENT):
            return self._multi(path, _ASSIGNMENT, "assignmentRule",
                               "assignmentrule", _criteria_fields)
        if name.endswith(_ESCALATION):
            return self._multi(path, _ESCALATION, "escalationRule",
                               "escalationrule", _criteria_fields)
        if name.endswith(_MATCHING):
            return self._multi(path, _MATCHING, "matchingRules",
                               "matchingrule", _matching_fields)
        if name.endswith(_DUPLICATE):
            return self._duplicate(path)
        return [], []

    # --- multi-rule files: assignment / escalation / matching ----------------
    def _multi(self, path, suffix, wrapper, node_type, field_fn):
        # governed object is the filename stem before the suffix
        obj = path.name[: -len(suffix)]
        nodes: list[dict] = []
        edges: list[dict] = []
        try:
            root = ET.parse(path).getroot()
        except Exception:   # ParseError, FileNotFoundError, etc. — skip, never raise
            return nodes, edges

        for rule in _iter_local(root, wrapper):
            full = _text(rule, "fullName")
            if not full:
                continue
            rid = f"{node_type}/{obj}.{full}"
            nodes.append(node(rid, node_type, full))
            if obj:
                edges.append(raw_edge(rid, "on", "object", obj))
            for f in field_fn(rule):
                qual = _qual_field(f, obj)
                if qual:
                    edges.append(raw_edge(rid, "reads", "field", qual))
            # assignment / escalation rules route to queues and send templates
            if node_type in ("assignmentrule", "escalationrule"):
                _action_edges(rule, rid, obj, edges)

        return nodes, edges

    # --- single-rule file: duplicateRule -------------------------------------
    def _duplicate(self, path):
        # filename is "<Object>.<RuleName>.duplicateRule-meta.xml"; the stem before
        # the suffix is the rule fullName (which itself begins with the object).
        stem = path.name[: -len(_DUPLICATE)]
        nodes: list[dict] = []
        edges: list[dict] = []
        try:
            root = ET.parse(path).getroot()
        except Exception:   # ParseError, FileNotFoundError, etc. — skip, never raise
            return nodes, edges

        full = _text(root, "fullName") or stem
        if not full:
            return nodes, edges

        # object: the segment before the first "." in fullName, or a <sobjectType>
        # / <matchRuleSObjectType> if present. fullName "<Object>.<RuleName>".
        obj = ""
        if "." in full:
            obj = full.split(".", 1)[0]
        sobj = _text(root, "sobjectType")
        if not obj and sobj:
            obj = sobj
        if not obj:
            for mr in _iter_local(root, "duplicateRuleMatchRules"):
                t = _text(mr, "matchRuleSObjectType")
                if t:
                    obj = t
                    break

        rid = f"duplicaterule/{full}"
        nodes.append(node(rid, "duplicaterule", full))
        if obj:
            edges.append(raw_edge(rid, "on", "object", obj))

        # filter-item field NAMES -> reads -> field
        for f in _duplicate_fields(root):
            qual = _qual_field(f, obj)
            if qual:
                edges.append(raw_edge(rid, "reads", "field", qual))

        # composed matching rules -> references -> matchingrule. Each
        # <duplicateRuleMatchRules> names a <matchingRules> rule on a
        # <matchRuleSObjectType> (falling back to this rule's object).
        seen = set()
        for mr in _iter_local(root, "duplicateRuleMatchRules"):
            mname = _text(mr, "matchingRules")
            if not mname:
                continue
            mobj = _text(mr, "matchRuleSObjectType") or obj
            target = f"{mobj}.{mname}" if mobj else mname
            if target not in seen:
                seen.add(target)
                edges.append(raw_edge(rid, "references", "matchingrule", target))

        return nodes, edges


EXTRACTORS = [RuleExtractor()]
