"""Optional tree-sitter (concrete syntax tree) backend for the Apex extractor.

Emits the same node/edge vocabulary as the regex backend, more precisely, and as
a superset: every edge the regex backend produces for a source is also produced
here. Used only when a loadable apex parser is available; that parser is passed
in to :func:`extract_ast` so dispatch and this backend agree on which parser is
live.

Over the regex backend it gains: precise class/method declarations including
inner/nested classes; method invocations with no comment/string false positives
(the CST never tokenises text inside ``'...'`` or ``/* */`` as code); a local
symbol table (parameters + ``Type var`` declarations) so an instance call
``var.method()`` resolves to ``calls -> apexmethod/<Type>.method``; and SOQL/SOSL
walked structurally for ``reads -> object``/``field`` with no false positive from
the word ``from`` in a string.

Method signature attrs (``return_type``/``visibility``/``is_static``/
``parameters``/``overloads``) and the class ``sharing`` attr match the regex
backend's shape exactly (same names, same value forms); this backend additionally
emits ``start_line``/``end_line`` (1-based) on apexmethod nodes.
"""
from __future__ import annotations

import re
from pathlib import Path

from ...core import node, raw_edge
from ...salesforce import _strip_apex
from ._common import (
    _ACCESS_MODIFIERS,
    _ASYNC_IFACES,
    _COLLECTION_WRAPPERS,
    _METHOD_ANNOTATIONS,
    _SHARING_RE,
    _SOQL_FROM_RE,
    _async_iface_name,
    _node_kind_for,
    _norm_type,
)

# Method-level annotation allow-list for the AST backend — the shared set plus
# ``istest`` (the AST backend additionally keeps the @IsTest flag on methods).
_AST_METHOD_ANNOTATIONS = _METHOD_ANNOTATIONS + ("istest",)


def extract_ast(parser, path: Path):
    """Parse the class with the apex concrete syntax tree and emit ``(nodes,
    edges)``. A superset of the regex backend: every edge it produces for this
    source is also produced here. ``parser`` is the live apex parser, passed in so
    dispatch and this backend agree on which parser is live.

    Confidentiality: only names and structural relations are emitted. SOQL/SOSL
    are walked for object/field identifiers only — the SOSL search term and any
    literal/filter values are never read or emitted.
    """
    raw = path.read_text("utf-8", errors="replace")
    b = raw.encode("utf-8")
    tree = parser.parse(raw)
    root = tree.root_node()

    ctx = _AstCtx(b)

    # File-name fallback class name.
    cname = path.stem
    # The outermost type declaration gives the canonical class name + header.
    top = _first_type_decl(root)
    if top is not None:
        nm = _field_text(top, "name", b)
        if nm:
            cname = nm
    cid = f"apexclass/{cname}"

    nodes: list[dict] = [node(cid, "apexclass", cname, kind="class")]
    # class-level sharing modifier, read precisely from the top declaration's
    # modifiers (annotation children skipped so an annotation argument can't
    # fake the keyword).
    if top is not None:
        sharing = _ast_sharing(top, b)
        if sharing:
            nodes[0]["sharing"] = sharing
    edges: list[dict] = []
    async_kinds: list[str] = []

    # Pre-pass: per-class method names (for intra-class call resolution) and
    # the @IsTest flag (drives the `tests` edge).
    _ast_index_types(root, b, ctx)

    # Base `references -> object` set, matching the regex backend: every `\w+__c`
    # token plus every SOQL `FROM <obj>` target. Reads only names from the
    # comment-stripped source.
    try:
        stripped = _strip_apex(raw)
        for obj in re.findall(r"\b(\w+__c)\b", stripped):
            ctx.object_refs.add(obj)
        for obj in re.findall(r"\bFROM\s+(\w+)", stripped, re.I):
            ctx.object_refs.add(obj)
    except Exception:
        pass

    # Walk every type declaration (top-level + nested/inner). Each contributes
    # its own apexmethod nodes (keyed by the innermost class name); all
    # class-level edges (calls/references/extends/implements/async/tests) are
    # attributed to the top class id `cid` (whole-file attribution).
    _ast_walk_types(root, cid, cname, b, nodes, edges, async_kinds, ctx,
                    is_top=True)

    # Class-level reference/call/async sets aggregated across the file.
    for ref in sorted(ctx.class_refs):
        if ref and ref != cname:
            edges.append(raw_edge(cid, "calls", "apexclass", ref))
    for qm in sorted(ctx.qualified_calls):
        edges.append(raw_edge(cid, "calls", "apexmethod", f"{qm[0]}.{qm[1]}"))
    for obj in sorted(ctx.object_refs):
        edges.append(raw_edge(cid, "references", "object", obj))

    # @IsTest class -> `tests` edges to the classes it instantiates/exercises.
    if ctx.is_test_class:
        for tgt in sorted(ctx.tested_classes):
            if tgt and tgt != cname:
                edges.append(raw_edge(cid, "tests", "apexclass", tgt))

    # Class-level async (Batchable/Queueable/Schedulable on the top class).
    if async_kinds:
        nodes[0]["async_kind"] = sorted(set(async_kinds))
        for k in sorted(set(async_kinds)):
            edges.append(raw_edge(cid, "async", "apexclass", _async_iface_name(k)))

    # `kind` reflects the async interface the class implements: derive it from the
    # (generic-safe) `implements` edges, overriding the default above. Only
    # implements drive kind; @future and call-site async (which also populate
    # `async_kinds`) must not.
    impl_async = [_ASYNC_IFACES[e["to_name"]] for e in edges
                  if e["type"] == "implements" and e["to_name"] in _ASYNC_IFACES]
    nodes[0]["kind"] = _node_kind_for(impl_async)

    return nodes, edges


# ------------------------------------------------------------------ #
def _ast_index_types(n, b, ctx):
    """Pre-pass: record each class's method names (and per-name declaration
    counts, for the `overloads` attr) and whether the file is an @IsTest class,
    so the body walk can resolve self-calls and emit `tests`."""
    if n.kind() in ("class_declaration", "interface_declaration"):
        enclosing = _field_text(n, "name", b) or ""
        anns = _ast_annotations(n, b)
        if "istest" in anns:
            ctx.is_test_class = True
        body = n.child_by_field_name("body") or _named_child_of_kind(
            n, ("class_body", "interface_body", "enum_body"))
        if body is not None and enclosing:
            bucket = ctx.method_names_by_class.setdefault(enclosing, set())
            for i in range(body.child_count()):
                ch = body.child(i)
                if ch.kind() in ("method_declaration", "constructor_declaration"):
                    mn = _field_text(ch, "name", b)
                    if mn:
                        bucket.add(mn)
                        key = (enclosing, mn)
                        ctx.method_counts[key] = ctx.method_counts.get(key, 0) + 1
    for i in range(n.child_count()):
        _ast_index_types(n.child(i), b, ctx)


# ------------------------------------------------------------------ #
def _ast_walk_types(n, cid, top_name, b, nodes, edges, async_kinds,
                    ctx, is_top):
    """Recursively process class/interface declarations. `cid`/`top_name`
    always refer to the TOP class (where class-level edges are attributed),
    while method nodes/contains use the *enclosing* class name."""
    if n.kind() in ("class_declaration", "interface_declaration"):
        enclosing = _field_text(n, "name", b) or top_name
        # header: extends / implements -> attribute to the top class id.
        if is_top:
            _ast_header(n, cid, b, edges, async_kinds)
        body = n.child_by_field_name("body") or _named_child_of_kind(
            n, ("class_body", "interface_body", "enum_body"))
        if body is not None:
            for i in range(body.child_count()):
                ch = body.child(i)
                k = ch.kind()
                if k in ("method_declaration", "constructor_declaration"):
                    # constructors are methods named like their class (the regex
                    # backend emits them the same way — superset parity)
                    _ast_method(ch, cid, top_name, enclosing, b, nodes,
                                edges, async_kinds, ctx)
                elif k in ("class_declaration", "interface_declaration"):
                    # nested/inner type: recurse (not the top class).
                    _ast_walk_types(ch, cid, top_name, b, nodes, edges,
                                    async_kinds, ctx, is_top=False)
        return
    # not a type node: descend looking for the type declarations.
    for i in range(n.child_count()):
        _ast_walk_types(n.child(i), cid, top_name, b, nodes, edges,
                        async_kinds, ctx, is_top=is_top)


# ------------------------------------------------------------------ #
def _ast_header(cls_node, cid, b, edges, async_kinds):
    """extends/implements on the top class -> edges + async-kind detection."""
    sup = _named_child_of_kind(cls_node, ("superclass",))
    if sup is not None:
        for i in range(sup.child_count()):
            c = sup.child(i)
            if c.kind() in ("type_identifier", "scoped_type_identifier",
                            "generic_type"):
                name = _type_leaf_name(c, b)
                if name:
                    edges.append(raw_edge(cid, "extends", "apexclass", name))
                break
    ifaces = _named_child_of_kind(cls_node, ("interfaces",))
    if ifaces is not None:
        tlist = _named_child_of_kind(ifaces, ("type_list",))
        if tlist is not None:
            for i in range(tlist.child_count()):
                c = tlist.child(i)
                if c.kind() in ("type_identifier", "scoped_type_identifier",
                                "generic_type"):
                    name = _type_leaf_name(c, b)
                    if not name:
                        continue
                    edges.append(raw_edge(cid, "implements", "apexclass", name))
                    if name in _ASYNC_IFACES:
                        async_kinds.append(_ASYNC_IFACES[name])


# ------------------------------------------------------------------ #
def _ast_method(m, cid, top_name, enclosing, b, nodes, edges,
                async_kinds, ctx):
    """Emit one apexmethod node (+contains), its annotations, intra-class
    calls, qualified/instance calls, reads/writes, and async — using a local
    symbol table so `var.method()` resolves to the var's declared type."""
    mname = _field_text(m, "name", b)
    if not mname:
        return
    mid = f"apexmethod/{enclosing}.{mname}"

    anns = _ast_annotations(m, b)
    keep = sorted(a for a in anns if a in _AST_METHOD_ANNOTATIONS)

    # symbol table: params + locals -> var name -> sObject/class type. The same
    # formal_parameter nodes also yield the node's `parameters` attr precisely
    # (type text as written, whitespace-normalised) — same shape as the regex
    # backend's best-effort parse.
    symbols: dict[str, str] = {}
    parameters: list[dict] = []
    params = _named_child_of_kind(m, ("formal_parameters",))
    if params is not None:
        for i in range(params.child_count()):
            p = params.child(i)
            if p.kind() == "formal_parameter":
                pname = _field_text(p, "name", b)
                ptype = _ast_type_name(p.child_by_field_name("type"), b)
                if pname and ptype:
                    symbols[pname] = ptype
                raw_type = _norm_type(_field_text(p, "type", b))
                if pname and raw_type:
                    parameters.append({"type": raw_type, "name": pname})

    mnode = node(mid, "apexmethod", f"{enclosing}.{mname}")
    if keep:
        mnode["annotations"] = keep
    # signature detail (first declaration wins for overloads — the second
    # declaration's mnode below is discarded by the only-add-once gate).
    rtype = _norm_type(_field_text(m, "type", b))
    if rtype:
        mnode["return_type"] = rtype
    visibility, is_static = _ast_vis_static(m, b)
    if visibility:
        mnode["visibility"] = visibility
    if is_static:
        mnode["is_static"] = True
    if parameters:
        mnode["parameters"] = parameters
    n_decls = ctx.method_counts.get((enclosing, mname), 1)
    if n_decls > 1:
        mnode["overloads"] = n_decls
    # 1-based source lines of the declaration (modifiers/annotations included),
    # derived from byte offsets so no extra parser API is needed.
    mnode["start_line"] = b.count(b"\n", 0, m.start_byte()) + 1
    mnode["end_line"] = b.count(b"\n", 0, m.end_byte()) + 1
    # overloads collapse to one node id: only add the node once.
    if not any(x["id"] == mid for x in nodes):
        nodes.append(mnode)
        edges.append(raw_edge(cid, "contains", "apexmethod",
                              f"{enclosing}.{mname}"))

    # @future -> method-level async
    if "future" in anns:
        async_kinds.append("future")
        edges.append(raw_edge(mid, "async", "apexclass", "System.Future"))

    body = m.child_by_field_name("body") or _named_child_of_kind(
        m, ("block", "constructor_body"))
    if body is None:
        return
    # First pass: collect local declarations into the symbol table (so a call
    # earlier in the body can still resolve a later-typed var — best effort).
    _ast_collect_symbols(body, b, symbols)
    # Second pass: walk statements for calls / SOQL / DML / async.
    _ast_walk_method_body(body, mid, top_name, enclosing, b, edges,
                          async_kinds, symbols, ctx)


# ------------------------------------------------------------------ #
def _ast_collect_symbols(n, b, symbols):
    """Populate `symbols` from every local_variable_declaration in the subtree."""
    if n.kind() == "local_variable_declaration":
        tname = _ast_type_name(n.child_by_field_name("type"), b)
        if tname:
            for i in range(n.child_count()):
                d = n.child(i)
                if d.kind() == "variable_declarator":
                    vname = _field_text(d, "name", b)
                    if vname:
                        symbols.setdefault(vname, tname)
    for i in range(n.child_count()):
        _ast_collect_symbols(n.child(i), b, symbols)


# ------------------------------------------------------------------ #
def _ast_walk_method_body(n, mid, top_name, enclosing, b, edges,
                          async_kinds, symbols, ctx):
    """Walk a method body subtree emitting per-method edges."""
    k = n.kind()

    if k == "method_invocation":
        _ast_invocation(n, mid, top_name, enclosing, b, edges,
                        async_kinds, symbols, ctx)

    elif k == "query_expression":
        _ast_query(n, mid, b, edges, ctx)

    elif k == "dml_expression":
        obj = _ast_dml_object(n, b, symbols)
        if obj:
            edges.append(raw_edge(mid, "writes", "object", obj))

    elif k == "object_creation_expression":
        tname = _ast_type_name(n.child_by_field_name("type"), b)
        if tname and _looks_like_class(tname):
            ctx.class_refs.add(tname)
            if ctx.is_test_class:
                ctx.tested_classes.add(tname)

    for i in range(n.child_count()):
        _ast_walk_method_body(n.child(i), mid, top_name, enclosing, b,
                              edges, async_kinds, symbols, ctx)


# ------------------------------------------------------------------ #
def _ast_invocation(n, mid, top_name, enclosing, b, edges,
                    async_kinds, symbols, ctx):
    """One method_invocation -> calls edges (no comment/string false positives:
    the CST never tokenises string/comment text as code)."""
    name = _field_text(n, "name", b)
    if not name:
        return
    obj = n.child_by_field_name("object")

    if obj is None:
        # bare `foo(...)` — an intra-class self-call. Attribute method->method.
        if name in ctx.method_names_by_class.get(enclosing, set()):
            edges.append(raw_edge(mid, "calls", "apexmethod",
                                  f"{enclosing}.{name}"))
        return

    okind = obj.kind()
    otext = _text(obj, b)

    if okind == "this":
        if name in ctx.method_names_by_class.get(enclosing, set()):
            edges.append(raw_edge(mid, "calls", "apexmethod",
                                  f"{enclosing}.{name}"))
        return

    if okind == "identifier":
        ident = otext
        # (a) a typed local/parameter -> resolve to its declared type's method.
        typ = symbols.get(ident)
        if typ:
            short = typ.rsplit(".", 1)[-1]
            edges.append(raw_edge(mid, "calls", "apexmethod",
                                  f"{short}.{name}"))
            ctx.qualified_calls.add((short, name))
            if _looks_like_class(short):
                ctx.class_refs.add(short)
            return
        # (b) custom metadata / settings accessor -> references -> object.
        #     `Type__mdt.getAll|getInstance(...)`,
        #     `Settings__c.getInstance|getOrgDefaults|getValues(...)`.
        if _SOBJECT_SUFFIX_RE.search(ident):
            lname = name.lower()
            if ((ident.lower().endswith("__mdt")
                 and lname in ("getall", "getinstance"))
                or (ident.lower().endswith("__c")
                    and lname in ("getinstance", "getorgdefaults",
                                  "getvalues"))):
                ctx.object_refs.add(ident)
            return
        if _looks_like_class(ident):
            # (c) `ClassName.method(` — class-style static/qualified call.
            ctx.qualified_calls.add((ident, name))
            ctx.class_refs.add(ident)
            if ctx.is_test_class:
                ctx.tested_classes.add(ident)
            # dynamic SOQL: Database.query|getQueryLocator|countQuery('...FROM X')
            if (ident == "Database"
                    and name in ("query", "getQueryLocator", "countQuery")):
                _ast_dynamic_soql(n, mid, b, edges)
            # precise DML: Database.insert|update|delete|upsert|undelete(expr)
            if (ident == "Database"
                    and name in ("insert", "update", "delete", "upsert",
                                 "undelete")):
                obj = _ast_database_dml_object(n, b, symbols)
                if obj:
                    edges.append(raw_edge(mid, "writes", "object", obj))
            # async call-site detection (System.enqueueJob / Database.executeBatch
            # / System.schedule).
            _ast_callsite_async(ident, name, n, mid, b, edges, async_kinds)
        return

    # `a.b.method(` — qualified head; take the head's leaf identifier as class.
    if okind in ("field_access", "scoped_type_identifier",
                 "array_access"):
        head = _leaf_identifier(obj, b)
        if head and _looks_like_class(head):
            ctx.qualified_calls.add((head, name))
            ctx.class_refs.add(head)


# ------------------------------------------------------------------ #
def _ast_database_dml_object(inv, b, symbols) -> str:
    """sObject written by `Database.<dml>(expr)` — resolve the first arg from
    the symbol table (typed local/param) or a `new X()` operand."""
    args = inv.child_by_field_name("arguments")
    if args is None:
        return ""
    for i in range(args.child_count()):
        c = args.child(i)
        k = c.kind()
        if k == "object_creation_expression":
            t = _ast_type_name(c.child_by_field_name("type"), b)
            if t and t.lower() not in _COLLECTION_WRAPPERS:
                return t
            return ""
        if k == "identifier":
            typ = symbols.get(_text(c, b))
            return typ.rsplit(".", 1)[-1] if typ else ""
    return ""


# ------------------------------------------------------------------ #
def _ast_dynamic_soql(inv, mid, b, edges):
    """`Database.query|getQueryLocator|countQuery('... FROM Obj ...')` ->
    reads -> object. Only fires when the first arg is a string literal (a variable
    arg can't be resolved from the call site). Reads only the object name from the
    literal — never the query text/values."""
    args = inv.child_by_field_name("arguments")
    if args is None:
        return
    first = None
    for i in range(args.child_count()):
        c = args.child(i)
        if c.kind() not in ("(",):
            first = c
            break
    if first is None or first.kind() != "string_literal":
        return
    lit = _text(first, b)
    m = _SOQL_FROM_RE.search(lit)
    if m and m.group(1):
        edges.append(raw_edge(mid, "reads", "object", m.group(1)))


# ------------------------------------------------------------------ #
def _ast_callsite_async(ident, name, inv, mid, b, edges, async_kinds):
    """`System.enqueueJob` / `Database.executeBatch` / `System.schedule` ->
    async edge to the `new Foo()` operand class, else the framework iface."""
    key = (ident, name)
    kind = {
        ("System", "enqueueJob"): "queueable",
        ("Database", "executeBatch"): "batchable",
        ("System", "schedule"): "schedulable",
    }.get(key)
    if not kind:
        return
    async_kinds.append(kind)
    target = _async_iface_name(kind)
    args = inv.child_by_field_name("arguments")
    if args is not None:
        for i in range(args.child_count()):
            a = args.child(i)
            if a.kind() == "object_creation_expression":
                t = _ast_type_name(a.child_by_field_name("type"), b)
                if t and t.lower() not in _COLLECTION_WRAPPERS:
                    target = t
                    break
    edges.append(raw_edge(mid, "async", "apexclass", target))


# ------------------------------------------------------------------ #
def _ast_query(q, mid, b, edges, ctx):
    """Walk a SOQL/SOSL `query_expression` for reads -> object / field. Only
    identifiers are read; the SOSL search term and all literal VALUES are
    ignored (confidentiality)."""
    soql = _named_child_of_kind(q, ("soql_query_body",))
    if soql is not None:
        obj = _soql_object(soql, b)
        if obj:
            edges.append(raw_edge(mid, "reads", "object", obj))
            for fld in _soql_fields(soql, b):
                edges.append(raw_edge(mid, "reads", "field", f"{obj}.{fld}"))
        return
    sosl = _named_child_of_kind(q, ("sosl_query_body",))
    if sosl is not None:
        ret = _named_child_of_kind(sosl, ("returning_clause",))
        if ret is not None:
            for i in range(ret.child_count()):
                sr = ret.child(i)
                if sr.kind() == "sobject_return":
                    obj = _leaf_identifier(sr, b)
                    if obj:
                        edges.append(raw_edge(mid, "reads", "object", obj))


def _soql_object(soql, b) -> str:
    fc = _named_child_of_kind(soql, ("from_clause",))
    if fc is None:
        return ""
    for i in range(fc.child_count()):
        c = fc.child(i)
        if c.kind() in ("storage_identifier", "storage_alias"):
            ident = _leaf_identifier(c, b)
            if ident:
                return ident
    return ""


def _soql_fields(soql, b) -> list[str]:
    """Plain top-level field identifiers from the SELECT clause. Dotted
    relationship paths, aggregates (COUNT(...)) and aliases are skipped — the
    flat object is ambiguous for those, matching the regex backend."""
    sel = _named_child_of_kind(soql, ("select_clause",))
    if sel is None:
        return []
    out: list[str] = []
    for i in range(sel.child_count()):
        f = sel.child(i)
        if f.kind() == "field_identifier":
            # a clean column is a single identifier child; skip dotted paths.
            child0 = f.child(0) if f.child_count() else None
            if child0 is not None and child0.kind() == "identifier":
                name = _text(child0, b)
                if name and name not in out:
                    out.append(name)
    return out


# ------------------------------------------------------------------ #
def _ast_dml_object(dml, b, symbols) -> str:
    """sObject written by a `dml_expression` (`insert mp;` / `update new X();`).
    Resolves a bare identifier through the symbol table; reads a `new X()`
    operand's type directly; never reads literal VALUES."""
    for i in range(dml.child_count()):
        c = dml.child(i)
        k = c.kind()
        if k in ("dml_type", "insert", "update", "delete", "upsert",
                 "undelete", "merge"):
            continue
        if k == "object_creation_expression":
            t = _ast_type_name(c.child_by_field_name("type"), b)
            if t and t.lower() not in _COLLECTION_WRAPPERS:
                return t
        if k == "identifier":
            ident = _text(c, b)
            typ = symbols.get(ident)
            if typ:
                return typ.rsplit(".", 1)[-1]
            return ""
    return ""


# --------------------------------------------------------------------------- #
# AST helpers (only used when the tree-sitter backend is active).
# --------------------------------------------------------------------------- #
class _AstCtx:
    """Mutable scratch shared across the AST walk of one class file.

    - ``class_refs`` / ``qualified_calls`` / ``object_refs`` accumulate file-level
      edges (attributed to the top class id, mirroring the regex backend),
    - ``method_names_by_class`` lets a bare/`this.` call resolve to an intra-class
      method node,
    - ``method_counts`` maps ``(class, method)`` -> number of declarations seen,
      so an overloaded method's single node can carry ``overloads: N``,
    - ``is_test_class`` / ``tested_classes`` drive the ``tests`` edge for @IsTest.
    """

    def __init__(self, src_bytes: bytes):
        self.b = src_bytes
        self.class_refs: set[str] = set()
        self.qualified_calls: set[tuple[str, str]] = set()
        self.object_refs: set[str] = set()
        self.method_names_by_class: dict[str, set[str]] = {}
        self.method_counts: dict[tuple[str, str], int] = {}
        self.is_test_class: bool = False
        self.tested_classes: set[str] = set()


def _text(n, b: bytes) -> str:
    """Decoded source span for a node (best-effort)."""
    try:
        return b[n.start_byte():n.end_byte()].decode("utf-8", "replace")
    except Exception:
        return ""


def _field_text(n, field: str, b: bytes) -> str:
    c = n.child_by_field_name(field)
    return _text(c, b) if c is not None else ""


def _named_child_of_kind(n, kinds):
    """First direct child whose kind is in ``kinds`` (a tuple), else None."""
    for i in range(n.child_count()):
        c = n.child(i)
        if c.kind() in kinds:
            return c
    return None


def _first_type_decl(root):
    """The first class/interface declaration anywhere in the tree (the top type)."""
    stack = [root]
    while stack:
        n = stack.pop(0)
        if n.kind() in ("class_declaration", "interface_declaration"):
            return n
        for i in range(n.child_count()):
            stack.append(n.child(i))
    return None


def _leaf_identifier(n, b: bytes) -> str:
    """The first ``identifier``/``type_identifier`` token reachable from ``n``
    (depth-first). Used to take e.g. the leaf class of a dotted/qualified head or
    the object name out of a SOQL ``storage_identifier``."""
    if n is None:
        return ""
    if n.kind() in ("identifier", "type_identifier"):
        return _text(n, b)
    for i in range(n.child_count()):
        r = _leaf_identifier(n.child(i), b)
        if r:
            return r
    return ""


def _type_leaf_name(n, b: bytes) -> str:
    """Class name out of a (possibly generic / scoped) type node. For
    ``Database.Batchable<sObject>`` returns ``Batchable``; for ``Schedulable``
    returns ``Schedulable`` — i.e. the rightmost type identifier of the base."""
    if n is None:
        return ""
    k = n.kind()
    if k == "type_identifier":
        return _text(n, b)
    if k == "generic_type":
        base = _named_child_of_kind(n, ("scoped_type_identifier", "type_identifier"))
        return _type_leaf_name(base, b)
    if k == "scoped_type_identifier":
        # rightmost type_identifier
        last = ""
        for i in range(n.child_count()):
            c = n.child(i)
            if c.kind() == "type_identifier":
                last = _text(c, b)
        return last
    return _leaf_identifier(n, b)


def _ast_type_name(type_node, b: bytes) -> str:
    """sObject/class name for a declared type, unwrapping a single collection
    generic (``List<MeterPoint__c>`` -> ``MeterPoint__c``; ``Map<Id, Rate__c>`` ->
    ``Rate__c``). Returns '' for ``void``/primitives we don't track."""
    if type_node is None:
        return ""
    k = type_node.kind()
    if k == "type_identifier":
        name = _text(type_node, b)
        return "" if name.lower() == "id" else name
    if k == "scoped_type_identifier":
        return _type_leaf_name(type_node, b)
    if k == "generic_type":
        base = _named_child_of_kind(type_node, ("type_identifier",
                                                "scoped_type_identifier"))
        base_name = _type_leaf_name(base, b) if base is not None else ""
        if base_name.lower() in _COLLECTION_WRAPPERS:
            targs = _named_child_of_kind(type_node, ("type_arguments",))
            if targs is not None:
                # last type argument is the value type for Map<K,V>; for List/Set
                # it's the single element type.
                inner = None
                for i in range(targs.child_count()):
                    c = targs.child(i)
                    if c.kind() in ("type_identifier", "generic_type",
                                    "scoped_type_identifier"):
                        inner = c
                if inner is not None:
                    return _ast_type_name(inner, b)
            return ""
        return base_name
    return ""


def _ast_annotations(decl, b: bytes) -> set[str]:
    """Lowercased annotation names attached to a class/method declaration (read
    from its ``modifiers`` child). Only the annotation NAME is read — never its
    arguments/values."""
    out: set[str] = set()
    mods = _named_child_of_kind(decl, ("modifiers",))
    if mods is None:
        return out
    for i in range(mods.child_count()):
        c = mods.child(i)
        if c.kind() == "annotation":
            nm = _field_text(c, "name", b)
            if not nm:
                # fall back to the identifier child after '@'
                idc = _named_child_of_kind(c, ("identifier",))
                nm = _text(idc, b) if idc is not None else ""
            if nm:
                out.add(nm.lower())
    return out


def _modifier_text(decl, b: bytes) -> str:
    """The declaration's keyword modifiers as one lowercased string. Annotation
    children are skipped so an annotation argument can never fake a keyword."""
    mods = _named_child_of_kind(decl, ("modifiers",))
    if mods is None:
        return ""
    return " ".join(
        _text(mods.child(i), b)
        for i in range(mods.child_count())
        if mods.child(i).kind() not in ("annotation", "marker_annotation")
    ).lower()


def _ast_vis_static(decl, b: bytes) -> tuple[str, bool]:
    """(visibility, is_static) for a method declaration, read from its
    modifiers: visibility is the stated access modifier ('' when unstated)."""
    words = _modifier_text(decl, b).split()
    visibility = next((w for w in words if w in _ACCESS_MODIFIERS), "")
    return visibility, "static" in words


def _ast_sharing(cls_node, b: bytes) -> str:
    """The class's sharing modifier — "with"/"without"/"inherited" — or '' when
    unstated (the grammar keeps e.g. `without sharing` as one modifier node)."""
    m = _SHARING_RE.search(_modifier_text(cls_node, b))
    return m.group(1).lower() if m else ""


# sObject/custom-metadata suffixes — these are OBJECTS, never apex classes.
_SOBJECT_SUFFIX_RE = re.compile(r"__(?:c|mdt|e|x|b|share|history)$", re.I)


def _looks_like_class(name: str) -> bool:
    """A class-like reference for `calls`/`extends`/`implements`/`tests` targets:
    PascalCase AND not a custom-object/metadata token (``X__c``/``X__mdt`` are
    sObjects, surfaced as `references -> object` instead). Lowercase locals are
    excluded so we don't mint apexclass refs from instance variables."""
    if not name or _SOBJECT_SUFFIX_RE.search(name):
        return False
    return name[:1].isupper()
