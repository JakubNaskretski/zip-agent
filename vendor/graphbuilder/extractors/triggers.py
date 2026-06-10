"""Apex trigger extractor — `*.trigger`.

Emits a `trigger/<Name>` node with `on` -> object and `calls` -> apexclass /
apexmethod edges. `parse_trigger` gives name/sobject/events but no body refs, so
the body (`t.source`) is parsed here for handler delegation: `ClassName.method(`
yields a `calls` edge to both the `apexmethod` and its `apexclass`, and
`new ClassName(` yields a `calls` edge to the `apexclass`. The raw events string
is also split into a structured `event_list` attr on the node.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..core import node, raw_edge
from ..salesforce import _strip_apex, parse_trigger

# Apex reserved words that can precede "(" or appear as "Name.method(" / "new Name("
# but are NOT class references. Skipping them keeps the delegation edges accurate.
_KEYWORDS = frozenset({
    "if", "else", "for", "while", "do", "switch", "try", "catch", "finally",
    "return", "throw", "new", "system", "trigger", "insert", "update", "delete",
    "upsert", "undelete", "merge", "and", "or", "not", "null", "true", "false",
    "this", "super", "instanceof", "void", "static", "public", "private",
    "protected", "global", "override", "virtual", "abstract", "final", "with",
    "without", "sharing", "on",
})

# A `ClassName.method(` delegation call. Captures the (qualified) head and the
# final method name. The head may itself be dotted (e.g. `MyNs.Handler`), so the
# class is the head's last segment.
_DOTTED_CALL = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\.([A-Za-z_]\w*)\s*\(")

# A `new ClassName(` instantiation (the class may be namespaced/inner-dotted).
_NEW_CALL = re.compile(r"\bnew\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(")


def _split_events(events: str) -> list[str]:
    """`"before insert, after update"` -> ["before insert", "after update"]."""
    out: list[str] = []
    for part in events.split(","):
        norm = " ".join(part.split()).lower()
        if norm:
            out.append(norm)
    return out


def _body_after_header(stripped: str) -> str:
    """Return the trigger body, i.e. everything after the first `)` that closes
    the `trigger Name on Object(events)` header, so header tokens never leak in."""
    m = re.search(r"\btrigger\b.*?\)", stripped, re.S)
    return stripped[m.end():] if m else stripped


class TriggerExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(".trigger")

    def extract(self, path: Path):
        t = parse_trigger(path)
        tid = f"trigger/{t.name}"
        attrs = {"events": t.events}
        event_list = _split_events(t.events)
        if event_list:
            attrs["event_list"] = event_list
        nodes = [node(tid, "trigger", t.name, **attrs)]
        edges = []
        if t.sobject:
            edges.append(raw_edge(tid, "on", "object", t.sobject))
        for cls in sorted(t.class_refs):
            edges.append(raw_edge(tid, "calls", "apexclass", cls))

        # Parse the body for handler delegation.
        seen_method: set[str] = set()   # "Class.method" already emitted
        seen_class: set[str] = set()    # "Class" already emitted as a calls target
        try:
            body = _body_after_header(_strip_apex(t.source or ""))
        except Exception:
            body = ""

        # `new ClassName(` -> calls -> apexclass/ClassName
        for m in _NEW_CALL.finditer(body):
            cls = m.group(1).split(".")[-1]
            if not cls or cls.lower() in _KEYWORDS:
                continue
            if cls not in seen_class:
                seen_class.add(cls)
                edges.append(raw_edge(tid, "calls", "apexclass", cls))

        # `ClassName.method(` -> calls -> apexmethod/Class.method + apexclass/Class
        for m in _DOTTED_CALL.finditer(body):
            head, method = m.group(1), m.group(2)
            cls = head.split(".")[-1]   # last segment of the (possibly dotted) head
            if not cls or not method:
                continue
            if cls.lower() in _KEYWORDS or method.lower() in _KEYWORDS:
                continue
            qualified = f"{cls}.{method}"
            if qualified not in seen_method:
                seen_method.add(qualified)
                edges.append(raw_edge(tid, "calls", "apexmethod", qualified))
            if cls not in seen_class:
                seen_class.add(cls)
                edges.append(raw_edge(tid, "calls", "apexclass", cls))

        return nodes, edges


EXTRACTORS = [TriggerExtractor()]
