"""Extracts approval processes (`*.approvalProcess-meta.xml`).

A file `<Object>.<ProcessName>.approvalProcess-meta.xml` becomes an
`approvalprocess/<Object>.<ProcessName>` node with edges: `on` -> the object
(filename prefix); `reads` -> field for each bare `Field__c` token in entry/step
criteria formulas, named `<Object>.<Field>` (criteria evaluate on the process's
own record); `uses` -> emailtemplate (`<emailTemplate>`); `references` -> queue
(a `<approver type="queue">`).

Names and structure only. Formula text, criteria values, and literals are never
emitted; only bare `__c` field tokens are pulled from formulas for `reads` edges.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..xmlutil import child as _child

_SUFFIX = ".approvalProcess-meta.xml"

# A bare custom-field API token (e.g. `Amount__c`, `Is_Active__c`). Only `__c`
# tokens are picked up: bare standard fields can't be told apart from formula
# function names / literals without per-object schema, so they're skipped.
_FIELD_TOKEN = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*__c)\b")

# Tags whose text is a criteria formula scanned for field tokens. Carried on the
# process (entry) and on each step.
_FORMULA_TAGS = ("entryCriteria", "criteria", "formula", "filterFormula")


def _text(el, tag: str) -> str:
    c = _child(el, tag)
    return c.text if c is not None and c.text is not None else ""


def _split_filename(name: str) -> tuple[str, str]:
    """`<Object>.<ProcessName>.approvalProcess-meta.xml` -> (Object, ProcessName).

    The object is the first dot-segment; the process name is everything between
    that and the suffix (it may itself contain dots). Returns ("", "") if the
    name doesn't fit the expected shape."""
    if not name.endswith(_SUFFIX):
        return "", ""
    stem = name[: -len(_SUFFIX)]            # `<Object>.<ProcessName>`
    if "." not in stem:
        return "", ""
    obj, process = stem.split(".", 1)
    return obj, process


def _formula_texts(root) -> list[str]:
    """All criteria-formula text blocks anywhere in the process (entry + steps).

    Scans every element whose tag (namespace stripped) is in `_FORMULA_TAGS`,
    catching entry and per-step criteria regardless of nesting. Never raises."""
    out: list[str] = []
    try:
        for el in root.iter():
            tag = el.tag.rsplit("}", 1)[-1]   # strip `{namespace}`
            if tag in _FORMULA_TAGS and el.text and el.text.strip():
                out.append(el.text)
    except Exception:
        pass
    return out


def _field_tokens(formula: str) -> set:
    """Bare `Field__c` API tokens found in a criteria formula. Never raises."""
    if not formula:
        return set()
    try:
        return set(_FIELD_TOKEN.findall(formula))
    except Exception:
        return set()


class ApprovalProcessExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(_SUFFIX)

    def extract(self, path: Path):
        obj, process = _split_filename(path.name)
        if not obj or not process:
            return [], []

        apid = f"approvalprocess/{obj}.{process}"
        nodes = [node(apid, "approvalprocess", f"{obj}.{process}")]
        edges = [raw_edge(apid, "on", "object", obj)]

        # ---- entry/step criteria formulas -> reads -> field ------------------ #
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            return nodes, edges

        fields: set = set()
        for formula in _formula_texts(root):
            fields |= _field_tokens(formula)

        for fname in sorted(fields):
            edges.append(raw_edge(apid, "reads", "field", f"{obj}.{fname}"))

        # ---- email templates -> uses -> emailtemplate ------------------------ #
        # The process and its steps may name a request/approval email template.
        seen_t: set = set()
        for el in root.iter():
            if el.tag.rsplit("}", 1)[-1] == "emailTemplate":
                tname = (el.text or "").strip()
                if tname and tname not in seen_t:
                    seen_t.add(tname)
                    edges.append(raw_edge(apid, "uses", "emailtemplate", tname))

        # ---- queue approvers -> references -> queue -------------------------- #
        # Each <approver type="queue"><name>Q</name> routes the step to a queue.
        # User/manager/related-field approvers have no node kind -> skipped.
        seen_q: set = set()
        for ap in root.iter():
            if ap.tag.rsplit("}", 1)[-1] != "approver":
                continue
            if _text(ap, "type").lower() != "queue":
                continue
            qname = _text(ap, "name").strip()
            if qname and qname not in seen_q:
                seen_q.add(qname)
                edges.append(raw_edge(apid, "references", "queue", qname))

        return nodes, edges


EXTRACTORS = [ApprovalProcessExtractor()]
