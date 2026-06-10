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
