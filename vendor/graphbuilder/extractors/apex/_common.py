"""Shared constants and helpers for the Apex backends (regex + AST).

Imports nothing from its sibling submodules, so either backend can import it
without a cycle.
"""
from __future__ import annotations

import re

# Async marker interfaces -> the `async_kind` they imply.
_ASYNC_IFACES = {
    "Database.Batchable": "batchable",
    "Batchable": "batchable",
    "Queueable": "queueable",
    "Schedulable": "schedulable",
}

# Apex collection wrappers unwrapped when resolving an sObject type.
_COLLECTION_WRAPPERS = {"list", "set", "map", "iterable"}

# `FROM <obj>` in SOQL.
# TODO: FROM gives the object; SELECT-field reads and SOSL RETURNING targets are not handled
_SOQL_FROM_RE = re.compile(r"\bFROM\s+(\w+)", re.I)

# A string literal (single-quoted; Apex has no double-quoted strings). Handles
# escaped quotes via the doubled `''` and `\'` conventions, best-effort.
_STRING_LIT_RE = re.compile(r"'((?:[^'\\]|\\.|'')*)'")

# Method-level annotation allow-list (lowercased). Both backends keep exactly
# these on apexmethod nodes; the AST backend additionally keeps ``istest``.
_METHOD_ANNOTATIONS = (
    "invocablemethod", "auraenabled", "future",
    "testsetup", "testvisible", "remoteaction",
    "readonly", "httpget", "httppost", "httpput",
    "httpdelete", "httppatch", "namespaceaccessible",
)

# Custom-label references in Apex: `System.Label.Foo`, `Label.Foo`, `$Label.Foo`.
# Only the label name is captured; the label resolver normalises any prefix and a
# leading namespace.
_LABEL_REF = re.compile(r"(?:\$Label|System\.Label|Label)\.([A-Za-z_]\w*)")

# Apex access modifiers — the `visibility` attr values on apexmethod nodes
# (lowercased; omitted when the declaration states none).
_ACCESS_MODIFIERS = ("public", "private", "protected", "global")

# Class-level sharing modifier -> the apexclass `sharing` attr
# ("with"/"without"/"inherited"; omitted when unstated).
_SHARING_RE = re.compile(r"\b(with|without|inherited)\s+sharing\b", re.I)


def _norm_type(t: str) -> str:
    """A type as written, with whitespace normalised so both backends emit the
    identical string: runs of whitespace collapse to one space, generics pack
    (`Map<Id , Account >` -> `Map<Id, Account>`), `[ ]` -> `[]`."""
    t = re.sub(r"\s+", " ", t or "").strip()
    t = re.sub(r"\s*<\s*", "<", t)
    t = re.sub(r"\s*>", ">", t)
    t = re.sub(r"\s*,\s*", ", ", t)
    t = re.sub(r"\s*\[\s*\]", "[]", t)
    return t


def _parse_params(raw: str) -> list:
    """Best-effort ``[{"type": ..., "name": ...}, ...]`` from the text between a
    signature's parens (the regex backend's parameter parse; the AST backend
    reads precise ``formal_parameter`` nodes instead but emits the same shape).

    Splits on top-level commas only — a small bracket-depth scanner tracks
    ``<>`` nesting so a generic like ``Map<Id, List<Account>>`` is never split
    on its inner commas. Each piece must look like ``[final] <type> <name>``;
    a piece that doesn't fit is skipped (string-level parse, not a grammar)."""
    out: list = []
    raw = (raw or "").strip()
    if not raw:
        return out
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in raw:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    for part in parts:
        part = re.sub(r"^\s*final\s+", "", part.strip(), flags=re.I)
        m = re.fullmatch(r"(?s)(.+)\s+([A-Za-z_]\w*)", part)
        if not m:
            continue
        out.append({"type": _norm_type(m.group(1)), "name": m.group(2)})
    return out


def _strip_string_literals(text: str) -> str:
    """Blank out string-literal contents so identifier scans (calls, DML var
    operands) don't trip over text inside `'...'`. Preserves structure, not the
    bytes."""
    return _STRING_LIT_RE.sub(lambda m: "''", text)


def _async_iface_name(kind: str) -> str:
    return {
        "batchable": "Database.Batchable",
        "queueable": "Queueable",
        "schedulable": "Schedulable",
        "future": "System.Future",
    }.get(kind, kind)


def _node_kind_for(async_kinds) -> str:
    """The apexclass ``kind`` implied by the async interface the class implements:
    Batchable -> ``batch`` (takes precedence), Schedulable -> ``schedulable``, else
    ``class``. Pass only the implements-derived kinds: ``@future``/``queueable``
    and call-site mechanisms are async but do not change the class kind."""
    ks = set(async_kinds)
    if "batchable" in ks:
        return "batch"
    if "schedulable" in ks:
        return "schedulable"
    return "class"
