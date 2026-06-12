"""Regex/string backend for the Apex extractor — the guaranteed fallback.

Depends only on the standard library plus ``graphbuilder.salesforce``. Used
whenever the tree-sitter grammar is absent. The AST backend (``_ast``) emits a
superset of these edges. The entry point is :func:`extract_regex`; everything
else is a private helper.
"""
from __future__ import annotations

import re
from pathlib import Path

from ...core import node, raw_edge
from ...salesforce import _strip_apex, parse_apex
from ._common import (
    _ACCESS_MODIFIERS,
    _ASYNC_IFACES,
    _COLLECTION_WRAPPERS,
    _METHOD_ANNOTATIONS,
    _SHARING_RE,
    _SOQL_FROM_RE,
    _STRING_LIT_RE,
    _async_iface_name,
    _node_kind_for,
    _norm_type,
    _parse_params,
    _strip_string_literals,
)

# --- module-level regexes (compiled once) ----------------------------------- #

# A method signature: `... returnType name ( ... ) {` (or `;` for interface/
# abstract methods, which still become method nodes). Modifiers are optional (`*`)
# so interface methods and default-access class methods — which carry no access
# modifier — are matched too. The `returnType name(...)` shape plus the
# `_NOT_METHODS` guard keeps statements/calls (`return foo()`, `new X()`,
# `if (...)`) from masquerading as declarations.
_METHOD_RE = re.compile(
    r"(?P<modifiers>(?:(?:public|private|protected|global|static|virtual|abstract|"
    r"override|final|webservice|testmethod)\s+)*)"
    # `ret` is a single type token (ident, optional generic, optional []). It must
    # not span whitespace/newlines: with optional modifiers a greedy ret would
    # otherwise swallow annotations + modifiers across lines and mis-split.
    r"(?P<ret>[A-Za-z_][\w.]*(?:<[^{}();]*>)?(?:\[\s*\])?)\s+"
    r"(?P<name>\w+)\s*\((?P<params>[^)]*)\)\s*(?P<body>\{|;)",
    re.I,
)

# Apex reserved words that can look like a "return type + name (" but aren't methods.
_NOT_METHODS = {
    "if", "for", "while", "switch", "catch", "return", "new", "else", "do",
    "try", "finally", "throw", "synchronized", "super", "this",
}

_ANNOTATION_RE = re.compile(r"@(\w+)")
_DML_RE = re.compile(r"\b(insert|update|delete|upsert|undelete)\b", re.I)

# Class declaration header — robust to generics (`Database.Batchable<sObject>`).
# extends/implements are parsed here so async detection handles generic interfaces.
_CLASS_HEADER_RE = re.compile(
    r"\bclass\s+(?P<name>\w+)\s*"
    r"(?P<rest>(?:extends\s+[\w.<>,\s]+?|implements\s+[\w.<>,\s]+?)*)\s*\{",
    re.I,
)
_EXTENDS_RE = re.compile(r"\bextends\s+([\w.]+)", re.I)
_IMPLEMENTS_RE = re.compile(r"\bimplements\s+([\w.<>,\s]+?)(?=\bextends\b|\{|$)", re.I)

# --- higher-fidelity regexes ------------------------------------------------ #

# `ClassName.method(` — a qualified, class-style static/instance call. The
# qualifier must start uppercase (class-like) so we don't match `obj.method(`
# on a lowercase local. Both segments are plain identifiers.
_QUALIFIED_CALL_RE = re.compile(r"\b([A-Z]\w*)\.(\w+)\s*\(")

# Bracketed inline SOQL: `[ SELECT ... FROM Obj ... ]`. Lazy so nested brackets
# (rare) don't over-capture; we only need the SELECT…FROM Obj head.
_SOQL_BRACKET_RE = re.compile(r"\[\s*(SELECT\b.*?)\]", re.I | re.S)
# Within a SOQL string, capture the field list (between SELECT and FROM) and the
# first object after FROM.
_SOQL_SELECT_RE = re.compile(r"\bSELECT\b(.*?)\bFROM\s+(\w+)", re.I | re.S)

# Dynamic SOQL entry points whose first string arg carries the query text.
_DYNAMIC_SOQL_RE = re.compile(
    r"\bDatabase\s*\.\s*(?:query|getQueryLocator|countQuery)\s*\(", re.I)

# Call-site async entry points -> async_kind.
_CALLSITE_ASYNC = [
    (re.compile(r"\bSystem\s*\.\s*enqueueJob\s*\(", re.I), "queueable"),
    (re.compile(r"\bDatabase\s*\.\s*executeBatch\s*\(", re.I), "batchable"),
    (re.compile(r"\bSystem\s*\.\s*schedule\s*\(", re.I), "schedulable"),
]

# Custom metadata (`Type__mdt`) and custom settings (`Settings__c`) accessor
# patterns. We require one of the well-known static accessors so we don't fire on
# every `__c`/`__mdt` mention.
_MDT_ACCESS_RE = re.compile(
    r"\b(\w+__mdt)\s*\.\s*(?:getAll|getInstance)\s*\(", re.I)
_CSETTING_ACCESS_RE = re.compile(
    r"\b(\w+__c)\s*\.\s*(?:getInstance|getOrgDefaults|getValues)\s*\(", re.I)

# `Database.<dml>(...)` precise DML.
_DATABASE_DML_RE = re.compile(
    r"\bDatabase\s*\.\s*(insert|update|delete|upsert|undelete)\s*\(", re.I)


def _soql_fields(query: str) -> tuple[str, list[str]]:
    """From a SOQL body return (object, [field, ...]). Only plain, top-level
    field identifiers are kept — aggregates (`COUNT(...)`), subqueries
    (`(SELECT ...)`), `*` and dotted relationship paths are skipped (the object a
    dotted path targets is ambiguous from a flat parse)."""
    # Strip parenthesised groups (aggregate calls like COUNT(Id) and child
    # subqueries `(SELECT ... FROM Child__r)`) from the WHOLE query first, so the
    # SELECT…FROM match locks onto the outer object and no aggregate name leaks
    # into the field list. Repeat to collapse any nesting.
    flat_q = query
    for _ in range(5):
        stripped = re.sub(r"\([^()]*\)", " ", flat_q)
        if stripped == flat_q:
            break
        flat_q = stripped
    m = _SOQL_SELECT_RE.search(flat_q)
    if not m:
        return "", []
    obj = m.group(2)
    # The original (un-stripped) SELECT clause, to detect function calls
    # (`COUNT(...)`, `toLabel(...)`, `FORMAT(...)`) whose name would otherwise
    # survive as a bare token once the parens are removed.
    om = _SOQL_SELECT_RE.search(query)
    orig_clause = om.group(1) if om else m.group(1)
    func_names = {n.lower() for n in re.findall(r"\b(\w+)\s*\(", orig_clause)}
    fields: list[str] = []
    for raw in m.group(1).split(","):
        tok = raw.strip()
        # a clean column is a single identifier; skip dotted/relationship paths,
        # `*`, aliases ("Amt a"), function names, and residual punctuation.
        if (re.fullmatch(r"[A-Za-z]\w*", tok)
                and tok.lower() != "from"
                and tok.lower() not in func_names):
            fields.append(tok)
    return obj, fields


def _resolve_sobject_type(operand: str, type_map: dict) -> str:
    """Best-effort sObject name for a DML operand. `operand` is the raw text after
    the DML keyword/`(`. Tries: a `new Obj(`/`new List<Obj>` literal, a `__c`/`__mdt`
    token, or a known local-variable type from `type_map`. Returns '' if unsure."""
    operand = operand.strip()
    # `new Obj(...)` or `new List<Obj>{...}` literal
    mnew = re.search(r"\bnew\s+(?:(?:List|Set|Map)\s*<\s*(?:Id\s*,\s*)?)?([A-Za-z]\w*)",
                     operand, re.I)
    if mnew:
        cand = mnew.group(1)
        if cand.lower() not in _COLLECTION_WRAPPERS and cand.lower() != "id":
            return cand
    # explicit custom object/metadata token
    mtok = re.search(r"\b(\w+__(?:c|mdt))\b", operand, re.I)
    if mtok:
        return mtok.group(1)
    # a bare variable name we have a declared type for
    mvar = re.match(r"([A-Za-z_]\w*)", operand)
    if mvar:
        return type_map.get(mvar.group(1), "")
    return ""


def _local_type_map(body: str) -> dict:
    """Map local-variable name -> sObject type for typed declarations in a method
    body, e.g. `List<Acme__c> rows`, `Acme__c rec`, `Map<Id, Acme__c> byId`. Only
    sObject-ish types (custom `__c`/`__mdt`, or PascalCase non-collection) are kept
    so plain `Integer i` declarations are ignored at the DML resolution step."""
    out: dict = {}
    decl = re.compile(
        r"\b(?:List|Set|Map)\s*<\s*(?:Id\s*,\s*)?([A-Za-z]\w*)\s*>\s*(\w+)"
        r"|\b([A-Z]\w*__(?:c|mdt))\s+(\w+)"
        r"|\b([A-Z]\w*)\s+(\w+)\s*[=;]",
        re.I)
    for m in decl.finditer(body):
        if m.group(1) and m.group(2):
            typ, var = m.group(1), m.group(2)
        elif m.group(3) and m.group(4):
            typ, var = m.group(3), m.group(4)
        elif m.group(5) and m.group(6):
            typ, var = m.group(5), m.group(6)
        else:
            continue
        if typ.lower() in _COLLECTION_WRAPPERS or typ.lower() == "id":
            continue
        out.setdefault(var, typ)
    return out


def _parse_header(src: str) -> tuple[str, list[str]]:
    """Return (extends_name, implements_list) from the first class declaration,
    tolerating generics. Generic params are stripped from interface names."""
    m = _CLASS_HEADER_RE.search(src)
    rest = m.group("rest") if m else ""
    em = _EXTENDS_RE.search(rest)
    extends = em.group(1) if em else ""
    impls: list[str] = []
    im = _IMPLEMENTS_RE.search(rest)
    if im:
        for raw in im.group(1).split(","):
            base = re.sub(r"<.*?>", "", raw).strip()
            if base:
                impls.append(base)
    return extends, impls


def _balanced_body(src: str, open_idx: int) -> tuple[str, int]:
    """Return the text inside the `{...}` block whose `{` is at `open_idx`, and the
    index just past the matching `}`. Falls back gracefully on truncated source."""
    depth = 0
    i = open_idx
    n = len(src)
    while i < n:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[open_idx + 1 : i], i + 1
        i += 1
    return src[open_idx + 1 :], n


def _annotations_before(src: str, start: int) -> set[str]:
    """Annotations attached to the declaration that begins at/just before `start`.

    We scan the text immediately preceding the match; annotations live on the
    lines right above the signature (possibly several)."""
    head = src[:start]
    # take the tail after the previous statement/brace boundary
    boundary = max(head.rfind(";"), head.rfind("{"), head.rfind("}"))
    segment = head[boundary + 1 :]
    return {m.lower() for m in _ANNOTATION_RE.findall(segment)}


def _dml_targets(body: str) -> set[str]:
    """Best-effort object names written by DML statements. Maps the DML keyword's
    variable operand to a custom object when it's an obvious literal
    (`insert new Acme__c(...)`); otherwise falls back to any `__c` token in the
    statement. Object-level granularity."""
    targets: set[str] = set()
    for m in _DML_RE.finditer(body):
        tail = body[m.end():]
        stmt = tail.split(";", 1)[0]
        # `insert new Acme__c(...)` / `update new List<Acme__c>{...}`
        for obj in re.findall(r"\b(\w+__c)\b", stmt):
            targets.add(obj)
        mnew = re.search(r"\bnew\s+(?:List<)?([A-Z]\w*)\b", stmt)
        if mnew:
            cand = mnew.group(1)
            if cand not in ("List", "Map", "Set"):
                targets.add(cand)
    return targets


def _deep_reads_writes_async(mid, body, edges, async_kinds) -> None:
    """Per-method higher-fidelity edges: SOQL field selection, dynamic SOQL
    object reads, precise DML writes (typed-local resolution), and call-site
    async. Best-effort (caller also guards)."""
    # blank string contents so `'...FROM X...'` doesn't leak into code scans
    code = _strip_string_literals(body)
    type_map = _local_type_map(code)

    # SOQL field selection: [SELECT a, b FROM Obj] -> reads -> field Obj.a/Obj.b
    read_fields: set[str] = set()
    for bm in _SOQL_BRACKET_RE.finditer(body):
        obj, fields = _soql_fields(bm.group(1))
        if not obj:
            continue
        for fld in fields:
            read_fields.add(f"{obj}.{fld}")
    for fq in sorted(read_fields):
        edges.append(raw_edge(mid, "reads", "field", fq))

    # Dynamic SOQL: Database.query|getQueryLocator|countQuery('... FROM Obj ...')
    # Only when the query string is an *inline literal* first argument — if the
    # arg is a variable we can't know the text from this call site, so we skip
    # (object-level reads still pick up `FROM` tokens via _SOQL_FROM_RE above).
    dyn_objs: set[str] = set()
    for dm in _DYNAMIC_SOQL_RE.finditer(body):
        arg = body[dm.end():].lstrip()
        if not arg.startswith("'"):
            continue
        sm = _STRING_LIT_RE.match(arg)
        if not sm:
            continue
        fm = _SOQL_FROM_RE.search(sm.group(1))
        if fm and fm.group(1):
            dyn_objs.add(fm.group(1))
    for obj in sorted(dyn_objs):
        edges.append(raw_edge(mid, "reads", "object", obj))

    # Precise DML: Database.insert|update|...(expr) and bare insert|update|...
    dml_objs: set[str] = set()
    for dm in _DATABASE_DML_RE.finditer(code):
        operand = code[dm.end():].split(";", 1)[0]
        # take up to the matching/last close-paren chunk for the first arg region
        operand = operand.split(",", 1)[0]
        obj = _resolve_sobject_type(operand, type_map)
        if obj:
            dml_objs.add(obj)
    for bm in _DML_RE.finditer(code):
        # only treat as a statement-style DML if not part of `Database.<dml>(`
        pre = code[:bm.start()].rstrip()
        if pre.endswith(".") or re.search(r"Database\s*\.\s*$", pre, re.I):
            continue
        operand = code[bm.end():].split(";", 1)[0]
        obj = _resolve_sobject_type(operand, type_map)
        if obj:
            dml_objs.add(obj)
    for obj in sorted(dml_objs):
        edges.append(raw_edge(mid, "writes", "object", obj))

    # Call-site async: enqueueJob/executeBatch/schedule -> async edge (+kind).
    for rx, kind in _CALLSITE_ASYNC:
        for am in rx.finditer(code):
            async_kinds.append(kind)
            arg = code[am.end():].split(";", 1)[0]
            mnew = re.search(r"\bnew\s+([A-Za-z]\w*)", arg)
            if mnew and mnew.group(1).lower() not in _COLLECTION_WRAPPERS:
                target = mnew.group(1)
            else:
                target = _async_iface_name(kind)
            edges.append(raw_edge(mid, "async", "apexclass", target))


def _signature_attrs(m) -> dict:
    """Signature-detail node attrs for one ``_METHOD_RE`` match (empty values
    omitted): ``return_type`` (as written, whitespace-normalised),
    ``visibility``, ``is_static`` (present only when static) and ``parameters``
    (best-effort — see :func:`_parse_params`).

    Constructors match with the access modifier in the ``ret`` slot
    (``public AcmeCalc(...)`` -> modifiers empty, ret=``public``), so a
    modifier-valued ``ret`` means: visibility from ``ret``, no return_type."""
    attrs: dict = {}
    ret_raw = (m.group("ret") or "").strip()
    if ret_raw.lower() in _ACCESS_MODIFIERS:           # constructor shape
        attrs["visibility"] = ret_raw.lower()
    else:
        if ret_raw:
            attrs["return_type"] = _norm_type(ret_raw)
        mods = (m.group("modifiers") or "").lower().split()
        vis = next((w for w in mods if w in _ACCESS_MODIFIERS), "")
        if vis:
            attrs["visibility"] = vis
        if "static" in mods:
            attrs["is_static"] = True
    params = _parse_params(m.group("params"))
    if params:
        attrs["parameters"] = params
    return attrs


def _deep(src, cname, cid, nodes, edges, async_kinds) -> set[str]:
    """Emit apexmethod nodes, contains edges, intra-class calls, annotations,
    per-method reads/writes, and @future async. Returns the set of method names
    defined on this class (used to detect self-calls)."""
    methods: dict[str, dict] = {}     # name -> {annotations, body, sig, count}

    for m in _METHOD_RE.finditer(src):
        name = m.group("name")
        ret = (m.group("ret") or "").strip().split()[-1] if m.group("ret") else ""
        if name.lower() in _NOT_METHODS or ret.lower() in _NOT_METHODS:
            continue
        # constructors are kept as method nodes; a bare `new Foo(` can't match
        # here because of the required leading modifier/return-type shape.
        anns = _annotations_before(src, m.start())
        if m.group("body") == "{":
            body, _ = _balanced_body(src, m.end() - 1)
        else:
            body = ""
        # overloads collapse onto one node: merge annotations + bodies (edge
        # behaviour); signature attrs are first-declaration-wins, with the
        # declaration count surfacing as `overloads` when >1.
        entry = methods.setdefault(name, {"annotations": set(), "body": "",
                                          "sig": None, "count": 0})
        entry["annotations"] |= anns
        entry["body"] += "\n" + body
        entry["count"] += 1
        if entry["sig"] is None:
            try:
                entry["sig"] = _signature_attrs(m)
            except Exception:
                entry["sig"] = {}

    for name, info in methods.items():
        mid = f"apexmethod/{cname}.{name}"
        anns = sorted(a for a in info["annotations"]
                      if a in _METHOD_ANNOTATIONS)
        mnode = node(mid, "apexmethod", f"{cname}.{name}")
        mnode.update(info["sig"] or {})
        if anns:
            mnode["annotations"] = anns
        if info["count"] > 1:
            mnode["overloads"] = info["count"]
        nodes.append(mnode)
        edges.append(raw_edge(cid, "contains", "apexmethod", f"{cname}.{name}"))

        body = info["body"]

        # @future is a method-level async mechanism
        if "future" in info["annotations"]:
            async_kinds.append("future")
            edges.append(raw_edge(mid, "async", "apexclass", "System.Future"))

        # per-method reads (SOQL FROM) / writes (DML), object-level
        for obj in sorted(set(_SOQL_FROM_RE.findall(body))):
            if obj:
                edges.append(raw_edge(mid, "reads", "object", obj))
        for obj in sorted(_dml_targets(body)):
            if obj:
                edges.append(raw_edge(mid, "writes", "object", obj))

        # --- higher-fidelity, per-method --- #
        try:
            _deep_reads_writes_async(mid, body, edges, async_kinds)
        except Exception:
            pass

    # intra-class method -> method calls
    names = set(methods)
    for name, info in methods.items():
        mid = f"apexmethod/{cname}.{name}"
        called = set()
        for callee in re.findall(r"(?:\bthis\.)?(\w+)\s*\(", info["body"]):
            if callee in names and callee != name:
                called.add(callee)
        for callee in sorted(called):
            edges.append(raw_edge(mid, "calls", "apexmethod", f"{cname}.{callee}"))

    return names


def extract_regex(path: Path):
    """Regex/string backend entry point. Returns ``(nodes, edges)`` for the class
    at ``path``."""
    try:
        cls = parse_apex(path)
    except Exception:
        return [], []

    cname = cls.name
    cid = f"apexclass/{cname}"
    src = _strip_apex(cls.source or "")

    # extends/implements: merge the base parser's result with the generic-safe
    # header parse (the parser drops interfaces that carry a `<...>` generic).
    h_extends, h_impls = "", []
    try:
        h_extends, h_impls = _parse_header(src)
    except Exception:
        pass
    extends = cls.extends or h_extends
    implements: list[str] = list(cls.implements or [])
    for i in h_impls:
        if i not in implements:
            implements.append(i)

    async_kinds: list[str] = []
    for impl in implements:
        key = impl.strip()
        short = key.rsplit(".", 1)[-1]
        if key in _ASYNC_IFACES:
            async_kinds.append(_ASYNC_IFACES[key])
        elif short in _ASYNC_IFACES:
            async_kinds.append(_ASYNC_IFACES[short])

    cnode_attrs = {}
    # class-level sharing modifier (`with|without|inherited sharing`), read from
    # the declaration header — everything before the class body's first `{`, so
    # an inner class's sharing modifier can never leak onto the top class.
    sm = _SHARING_RE.search(src.split("{", 1)[0])
    if sm:
        cnode_attrs["sharing"] = sm.group(1).lower()
    # `kind` reflects the async interface the class implements. Derived from the
    # generic-safe async detection (async_kinds is implements-only here;
    # @future/call-site kinds are appended later in `_deep`), so generic
    # interfaces like `Database.Batchable<sObject>` are still classified.
    nodes = [node(cid, "apexclass", cname, kind=_node_kind_for(async_kinds))]
    edges = []

    # ---- base layer (the parser's resolved sets) ---- #
    # `class_refs` is currently empty (`parse_apex` does not populate it), so this
    # loop emits nothing — class-level `calls` come from the deep pass below. Kept
    # so wiring it up later needs no change here.
    for ref in sorted(cls.class_refs or set()):
        if ref and ref != cname:
            edges.append(raw_edge(cid, "calls", "apexclass", ref))
    for obj in sorted(cls.sobject_refs or set()):
        if obj:
            edges.append(raw_edge(cid, "references", "object", obj))

    # ---- class -> class extends / implements ---- #
    if extends:
        edges.append(raw_edge(cid, "extends", "apexclass", extends.rsplit(".", 1)[-1]))
    for impl in implements:
        i = impl.strip()
        if i:
            edges.append(raw_edge(cid, "implements", "apexclass", i.rsplit(".", 1)[-1]))

    # ---- class-level higher-fidelity passes (whole source) ---- #
    # string-blanked view so `'... FROM X ...'` text can't masquerade as code
    try:
        code = _strip_string_literals(src)
    except Exception:
        code = src

    # TODO: richer intra-class call chains/sequencing could aid the agent (enhancement)
    # qualified `ClassName.method(` -> method-level `calls` (in addition to the
    # base class-level `calls`). Self-class qualifier is skipped (intra-class
    # self-calls are emitted per-method in `_deep`).
    try:
        seen_qcalls: set[tuple[str, str]] = set()
        for qm in _QUALIFIED_CALL_RE.finditer(code):
            qual, meth = qm.group(1), qm.group(2)
            if qual == cname:
                continue
            key = (qual, meth)
            if key in seen_qcalls:
                continue
            seen_qcalls.add(key)
            edges.append(raw_edge(cid, "calls", "apexmethod", f"{qual}.{meth}"))
    except Exception:
        pass

    # custom metadata / settings accessors -> references -> object
    try:
        refs: set[str] = set()
        for mm in _MDT_ACCESS_RE.finditer(code):
            refs.add(mm.group(1))
        for cm in _CSETTING_ACCESS_RE.finditer(code):
            refs.add(cm.group(1))
        for obj in sorted(refs):
            edges.append(raw_edge(cid, "references", "object", obj))
    except Exception:
        pass

    # ---- deep parse on the (comment-stripped) source ---- #
    try:
        method_names = _deep(src, cname, cid, nodes, edges, async_kinds)
    except Exception:
        method_names = set()

    # TODO: capture outbound HTTP/API callouts as a structure-only flag (never the endpoint/credential)
    # de-dup + record async kinds on the class node
    if async_kinds:
        cnode_attrs["async_kind"] = sorted(set(async_kinds))
        # class-level async: the class depends on the async framework it implements
        for k in sorted(set(async_kinds)):
            edges.append(raw_edge(cid, "async", "apexclass", _async_iface_name(k)))
    if cnode_attrs:
        nodes[0].update(cnode_attrs)

    return nodes, edges
