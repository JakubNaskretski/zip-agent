"""DataWeave (``.dwl``) parsing — turn a DataWeave script's HEADER into a typed
record (Phase-5 Mule taxonomy).

A ``.dwl`` file is a DataWeave script: an optional *header* of declarations
(``%dw`` version, ``input``/``output`` directives, ``import``s, ``var``/``fun``
definitions) ended by a ``---`` separator, then the body expression. A *module*
file — one that other scripts ``import`` — commonly has only declarations and no
``---``.

We parse the HEADER only — the stable, line-oriented declaration grammar — after
stripping ``//`` line and ``/* */`` block comments. The body expression (the
actual transformation logic) is deliberately NOT parsed here: that is the future
optional tree-sitter backend, mirroring the Apex ``_regex`` (always-on) vs
``_ast`` (optional) split. Like the rest of this package the parse is
dependency-free stdlib and captures structural names only — module paths,
function names, mime types — never DataWeave values or body logic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .mulesoft import dataweave_module, dataweave_rel_path

# A ``::``-qualified (or bare) DataWeave module path: dw::core::Strings, Common.
_MODULE = r"[A-Za-z_$][\w$]*(?:::[A-Za-z_$][\w$]*)*"
# A media/mime token, stopping at a `;` parameter (application/json; charset=…).
_MIME = r"[\w.+-]+/[\w.+-]+"
_SEPARATOR = re.compile(r"^[ \t]*---[ \t]*$", re.MULTILINE)
# `import [<names> from ]<module>[ as <Alias>]` — the module is what we keep.
# Single-line form (the universal DataWeave style); a rare import wrapped across
# lines is a documented gap, not broadened here (a looser regex risks false hits).
_IMPORT_RE = re.compile(
    r"^[ \t]*import[ \t]+(?:.+?[ \t]+from[ \t]+)?(" + _MODULE + r")"
    r"(?:[ \t]+as[ \t]+[\w$]+)?[ \t]*$", re.MULTILINE)
# `fun name(` — optionally preceded by annotations (`@TailRec`, `@Foo(...)`).
_FUN_RE = re.compile(
    r"^[ \t]*(?:@[\w.]+[ \t]*(?:\([^)]*\))?[ \t]*)*fun[ \t]+([A-Za-z_$][\w$]*)",
    re.MULTILINE)
_OUTPUT_RE = re.compile(r"^[ \t]*output[ \t]+(" + _MIME + r")", re.MULTILINE)
_INPUT_RE = re.compile(r"^[ \t]*input[ \t]+([A-Za-z_$][\w$]*)[ \t]+(" + _MIME + r")",
                       re.MULTILINE)
_VERSION_RE = re.compile(r"^[ \t]*%dw[ \t]+([\d.]+)", re.MULTILINE)


@dataclass
class DataWeaveScript:
    file: str = ""                                  # rel path under src/<main|test>
    module: str = ""                                # importable name, e.g. modules::Common
    version: str = ""                               # %dw version, e.g. 2.0
    output: str = ""                                # output mime, e.g. application/json
    inputs: list = field(default_factory=list)      # input-directive names
    imports: list = field(default_factory=list)     # imported module paths
    funcs: list = field(default_factory=list)       # top-level fun names


def _strip_comments(text: str) -> str:
    """Drop ``//`` line and ``/* */`` block comments while LEAVING string
    literals intact — a naive regex would corrupt a ``var x = "a // b"`` or
    ``"a /* b */ c"``. A small char scanner tracks ``"``/``'`` quoting (with
    backslash escapes) and only strips comment delimiters outside strings.
    (A ``/regex/`` literal in the header is a rare unhandled edge; the body,
    where regexes usually live, is not parsed.)"""
    out: list = []
    i, n, quote = 0, len(text), None
    while i < n:
        c = text[i]
        if quote is not None:                          # inside a string literal
            out.append(c)
            if c == "\\" and i + 1 < n:                 # escape: keep next char
                out.append(text[i + 1]); i += 2; continue
            if c == quote:
                quote = None
            i += 1; continue
        if c in ('"', "'"):
            quote = c; out.append(c); i += 1; continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":   # line comment
            j = text.find("\n", i)
            if j == -1:
                break
            i = j; continue                             # keep the newline
        if c == "/" and i + 1 < n and text[i + 1] == "*":   # block comment
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        out.append(c); i += 1
    return "".join(out)


def _header(text: str) -> str:
    """The declaration header: everything before the first line that is exactly
    ``---`` (the body separator). A module file with no separator is all header."""
    m = _SEPARATOR.search(text)
    return text[:m.start()] if m else text


def _dedup(values) -> list:
    out, seen = [], set()
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def parse_dataweave(path: Path) -> DataWeaveScript:
    """Parse one ``.dwl`` file's header into a :class:`DataWeaveScript`. Never
    raises: an unreadable file yields an empty-but-located record."""
    try:
        raw = path.read_text("utf-8", errors="replace")
    except OSError:
        raw = ""
    header = _header(_strip_comments(raw))
    version = _VERSION_RE.search(header)
    output = _OUTPUT_RE.search(header)
    return DataWeaveScript(
        file=dataweave_rel_path(path),
        module=dataweave_module(path),
        version=version.group(1) if version else "",
        output=output.group(1) if output else "",
        inputs=_dedup(m.group(1) for m in _INPUT_RE.finditer(header)),
        imports=_dedup(_IMPORT_RE.findall(header)),
        funcs=_dedup(_FUN_RE.findall(header)),
    )
