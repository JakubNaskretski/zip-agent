"""Office-document parsers — turn ``.docx`` / ``.xlsx`` files into typed dataclasses.

``docs`` is its own SEPARATE source (the fifth, after Salesforce / Confluence /
Jira / MuleSoft): stdlib-only ``zipfile`` + ``xml.etree`` over the OOXML parts,
with the parser -> dataclass split mirroring :mod:`graphbuilder.salesforce`. The
extractors (``extractors/docx.py``, ``extractors/xlsx.py``) turn these shapes
into nodes/edges.

Structure detection is TIERED — heterogeneous real-world documents must never be
guessed at uniformly:

  T1 DECLARED   (trusted; the default)  Word ``w:pStyle`` Heading1-9 / Title and
      an explicit ``w:outlineLvl``; Excel Table parts (``xl/tables/*.xml`` — the
      header row is *declared* there, not guessed).
  T2 HEURISTIC  (emitted with ``confidence: "heuristic"``, mirroring the joins'
      via/confidence)  Word bold-short-paragraph sections, applied ONLY when the
      document declares zero T1 headings (tiers never mix in one text flow);
      Excel first-row-as-header on sheets with no declared table, accepted only
      under the gate documented at the heuristic itself.
  T3 NONE       (honest flat)  no detectable structure -> the ``docfile`` node
      alone, ``structure: "none"``; sections are NEVER fabricated.

Confidentiality (hard rules, sharpening the engine-wide names-only policy):
NO cell values, NO formula bodies, NO author names. What may enter the graph:
names/labels/headers (sheet, table, column, defined names, heading titles) and —
the one deliberate content capture, like Confluence page bodies — Word section
text. References detected in that text (Jira keys, ``X__c`` API names, URLs)
become ATTRS only, never edges (domain isolation, same as Confluence
``jira_keys``).

A corrupt zip or malformed XML RAISES out of these parsers — the core records
the file in ``errors``, so one bad file never kills a build.
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .xmlutil import child, children, iter_local, local_name


# --------------------------------------------------------------------------- #
# shared helpers — file identity, detected refs, namespace-agnostic XML access
# --------------------------------------------------------------------------- #
def file_id(data: bytes) -> str:
    """First 12 hex chars of the file bytes' SHA-1 — the ``docfile/<id>`` name
    segment (and the prefix of every section/sheet/table id inside the file).
    Content-keyed identity makes a rename a non-event and dedup of an identical
    copy natural; the filename stays visible as the node label + source_path."""
    return hashlib.sha1(data).hexdigest()[:12]


_JIRA_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
_SF_NAME = re.compile(r"\b\w+__c\b")
_URL = re.compile(r"https?://[^\s<>\"']+")


def detect_refs(text: str) -> dict:
    """Cross-source references DETECTED in text/names — Jira issue keys, custom
    Salesforce API names (``X__c``) and plain URLs. They surface as node ATTRS
    only, never edges: wiring ``docs`` to other sources would be a deliberate
    later join step, exactly like Confluence ``jira_keys``. Deduped, first-seen
    order; keys with no hits are absent (attrs only when non-empty)."""
    out: dict = {}
    keys = list(dict.fromkeys(m.group(0) for m in _JIRA_KEY.finditer(text or "")))
    if keys:
        out["jira_keys"] = keys
    names = list(dict.fromkeys(m.group(0) for m in _SF_NAME.finditer(text or "")))
    if names:
        out["sf_names"] = names
    urls = list(dict.fromkeys(
        u for u in (m.group(0).rstrip(".,;:!?)") for m in _URL.finditer(text or "")) if u))
    if urls:
        out["urls"] = urls
    return out


def _xml_root(data: bytes):
    """Parse one zip part, tolerant of junk before the XML declaration (a BOM,
    stray whitespace) like :func:`graphbuilder.xmlutil.parse_root`; genuinely
    malformed XML still raises so the build records the file in ``errors``.

    Stdlib ``xml.etree`` on purpose (the engine is dependency-free, like every
    other extractor): it does not fetch external entities (an undefined entity
    is a ``ParseError`` -> an ``errors`` entry), and an entity-expansion bomb
    in a local file aborts only that file's extraction, never the build."""
    import xml.etree.ElementTree as ET

    return ET.fromstring(data.lstrip(b"\xef\xbb\xbf\xff\xfe\r\n\t "))


def _attr(el, name: str) -> str:
    """Attribute value matched by LOCAL name (``w:val`` / ``r:id`` / plain
    ``val`` all match ``val``) — the attribute-side twin of xmlutil's
    namespace-agnostic element helpers."""
    if el is None:
        return ""
    for k, v in el.attrib.items():
        if k.rsplit("}", 1)[-1] == name:
            return v
    return ""


def _relationships(data: bytes) -> list:
    """``(Id, Type, Target, TargetMode)`` of every relationship in a ``.rels``
    part, file order. Relationship attributes are unprefixed, so plain ``get``."""
    return [(rel.get("Id") or "", rel.get("Type") or "",
             (rel.get("Target") or "").strip(), rel.get("TargetMode") or "")
            for rel in _xml_root(data).iter()
            if local_name(rel.tag) == "Relationship"]


def _resolve_part(base_dir: str, target: str) -> str:
    """A rels ``Target`` resolved to a zip member name: relative to the owning
    part's directory (``../tables/table1.xml``); a package-absolute ``/xl/...``
    target keeps its own path."""
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(base_dir, target))


def _external_link_targets(data: bytes) -> list:
    """``Target`` of every external hyperlink relationship in a ``.rels`` part
    (dedup, file order) — link URLs live in the rels, not the document body."""
    return list(dict.fromkeys(
        target for _rid, rtype, target, mode in _relationships(data)
        if rtype.endswith("/hyperlink") and mode == "External" and target))


def _core_props(data: bytes, doc) -> None:
    """``dc:title`` + ``dcterms:modified`` from ``docProps/core.xml``.
    ``dc:creator`` / ``cp:lastModifiedBy`` are author NAMES — deliberately never
    read (anonymization by default)."""
    for el in _xml_root(data).iter():
        ln = local_name(el.tag)
        if ln == "title" and not doc.title:
            doc.title = (el.text or "").strip()
        elif ln == "modified":
            doc.modified = (el.text or "").strip()


# --------------------------------------------------------------------------- #
# Word (.docx) — parsed shapes
# --------------------------------------------------------------------------- #
@dataclass
class DocSection:
    title: str
    level: int = 1
    ordinal: int = 0       # 1-based document order — the `docsection/<id>#<n>` segment
    parent: int = 0        # owning section's ordinal; 0 = directly under the docfile
    confidence: str = ""   # "" = declared (T1, the trusted default) | "heuristic" (T2)
    text: str = ""         # body paragraphs up to the next same-or-higher-level heading
    columns: list = field(default_factory=list)   # first owned table's header row (names only)


@dataclass
class DocxDoc:
    file_id: str = ""
    structure: str = "none"    # declared | heuristic | none (the tier that produced sections)
    title: str = ""            # docProps dc:title (never the author)
    modified: str = ""         # docProps dcterms:modified (ISO string)
    sections: list = field(default_factory=list)   # list[DocSection], document order
    text: str = ""             # preamble before the first heading; the whole body when flat
    columns: list = field(default_factory=list)    # header of a table owned by no section
    urls: list = field(default_factory=list)       # hyperlink rels + URLs found in text
    jira_keys: list = field(default_factory=list)
    sf_names: list = field(default_factory=list)


# T2 bold-short-paragraph threshold: headings are short title lines (they fit
# well under a line of text), bolded prose runs longer and ends like a sentence.
# < 80 chars keeps real titles and rejects bolded sentences; a trailing period
# rejects bold emphasis that is still prose. Tuned ONCE from real-data evidence
# later (plan phase O5), not speculatively.
T2_MAX_CHARS = 80

_HEADING_STYLE = re.compile(r"(?i)^heading\s?([1-9])$")
_BOLD_OFF = ("0", "false", "off", "none")


def _para_text(p) -> str:
    """Joined text of every ``w:t`` under a paragraph (hyperlink runs included)."""
    return "".join((t.text or "") for t in p.iter() if local_name(t.tag) == "t").strip()


def _heading_level(p) -> int:
    """T1 mark: an explicit Heading1-9 / Title style, or an explicit
    ``w:outlineLvl`` (0-8 -> level 1-9). 0 = not a declared heading. Localized
    style ids that carry their outline level only in styles.xml are out of
    scope for v1 (they fall through to T2/T3 honestly)."""
    ppr = child(p, "pPr")
    if ppr is None:
        return 0
    style_val = _attr(child(ppr, "pStyle"), "val")
    m = _HEADING_STYLE.match(style_val)
    if m:
        return int(m.group(1))
    if style_val.lower() == "title":
        return 1
    lvl = child(ppr, "outlineLvl")
    if lvl is not None:
        try:
            v = int(_attr(lvl, "val"))
        except ValueError:
            return 0
        if 0 <= v <= 8:
            return v + 1
    return 0


def _is_bold(rpr, default: bool = False) -> bool:
    """OOXML on/off semantics for ``w:b``: present without a val = on; an
    explicit off value = off; absent = the caller's default (paragraph mark)."""
    if rpr is None:
        return default
    b = child(rpr, "b")
    if b is None:
        return default
    return _attr(b, "val").lower() not in _BOLD_OFF


def _bold_short(p, text: str) -> bool:
    """T2 candidate: a short (< :data:`T2_MAX_CHARS`), all-bold paragraph with
    no trailing period — the visual way ad-hoc documents fake headings. Every
    text-bearing run must be bold (run-level ``w:b``, falling back to the
    paragraph mark's)."""
    if not text or len(text) >= T2_MAX_CHARS or text.endswith("."):
        return False
    ppr = child(p, "pPr")
    para_bold = _is_bold(child(ppr, "rPr") if ppr is not None else None)
    saw_text = False
    for r in (el for el in p.iter() if local_name(el.tag) == "r"):
        if not "".join((t.text or "") for t in r.iter() if local_name(t.tag) == "t").strip():
            continue
        saw_text = True
        if not _is_bold(child(r, "rPr"), default=para_bold):
            return False
    return saw_text


def _table_columns(tbl) -> list:
    """First-row cell texts of a Word table — header NAMES only (the agreed
    capture; the table's data rows never enter the graph). Empty cells drop."""
    tr = next((el for el in tbl.iter() if local_name(el.tag) == "tr"), None)
    if tr is None:
        return []
    cols = []
    for tc in (c for c in tr if local_name(c.tag) == "tc"):
        txt = "".join((t.text or "") for t in tc.iter() if local_name(t.tag) == "t").strip()
        if txt:
            cols.append(txt)
    return cols


def parse_docx(path) -> DocxDoc:
    """Parse one ``.docx`` into a :class:`DocxDoc` (raises on a corrupt zip or
    malformed XML — the build records it in ``errors``)."""
    data = Path(path).read_bytes()
    doc = DocxDoc(file_id=file_id(data))
    rel_urls: list = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        root = _xml_root(zf.read("word/document.xml"))
        if "docProps/core.xml" in names:
            _core_props(zf.read("docProps/core.xml"), doc)
        if "word/_rels/document.xml.rels" in names:
            rel_urls = _external_link_targets(zf.read("word/_rels/document.xml.rels"))

    # one ordered pass over the body: paragraphs (text + T1 level + T2 flag)
    # and body-level tables (header row only)
    body = child(root, "body")
    blocks: list = []                       # ("p", text, t1_level, t2_flag) | ("tbl", columns, 0, False)
    for el in body if body is not None else ():
        ln = local_name(el.tag)
        if ln == "p":
            text = _para_text(el)
            blocks.append(("p", text, _heading_level(el), _bold_short(el, text)))
        elif ln == "tbl":
            blocks.append(("tbl", _table_columns(el), 0, False))

    # tier choice: declared headings win outright; the bold-short heuristic
    # applies ONLY when the document declares zero T1 headings (tiers never mix
    # in one text flow); otherwise the document honestly stays flat.
    marks = [(i, b[2]) for i, b in enumerate(blocks) if b[0] == "p" and b[2] > 0 and b[1]]
    confidence = ""
    if marks:
        doc.structure = "declared"
    else:
        marks = [(i, 1) for i, b in enumerate(blocks) if b[0] == "p" and b[3]]
        if marks:
            doc.structure = "heuristic"
            confidence = "heuristic"

    # sections: ordinal-stable ids, parentage via a level stack; section text =
    # body paragraphs up to the next same-or-higher-level heading (so a parent's
    # text spans its subsections' bodies — FTS over a section finds everything
    # under it; sub-heading LINES are structure, not body, and are excluded)
    mark_at = {i: lvl for i, lvl in marks}
    stack: list = []                        # [(level, ordinal)]
    by_ordinal: dict = {}
    for n, (i, lvl) in enumerate(marks, 1):
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        parent = stack[-1][1] if stack else 0
        texts = []
        for j in range(i + 1, len(blocks)):
            jl = mark_at.get(j)
            if jl is not None:
                if jl <= lvl:
                    break
                continue
            b = blocks[j]
            if b[0] == "p" and b[1]:
                texts.append(b[1])
        section = DocSection(title=blocks[i][1], level=lvl, ordinal=n, parent=parent,
                             confidence=confidence, text="\n".join(texts))
        doc.sections.append(section)
        by_ordinal[n] = section
        stack.append((lvl, n))

    # preamble (before any heading) belongs to the docfile itself; a flat
    # document's whole body lands there — the T3 content capture
    first_mark = marks[0][0] if marks else len(blocks)
    doc.text = "\n".join(b[1] for b in blocks[:first_mark] if b[0] == "p" and b[1])

    # each table's header row -> `columns` on the INNERMOST section open at its
    # position (the docfile when none); the first table per owner wins — one
    # columns attr per node, table NODES are not v1
    mark_ordinal = {i: n for n, (i, _) in enumerate(marks, 1)}
    current = 0
    for j, b in enumerate(blocks):
        if j in mark_ordinal:
            current = mark_ordinal[j]
        elif b[0] == "tbl" and b[1]:
            if current == 0:
                doc.columns = doc.columns or list(b[1])
            elif not by_ordinal[current].columns:
                by_ordinal[current].columns = list(b[1])

    # detected refs scan the WHOLE body text (headings included) — attrs only
    full_text = "\n".join(b[1] for b in blocks if b[0] == "p" and b[1])
    refs = detect_refs(full_text)
    doc.jira_keys = refs.get("jira_keys", [])
    doc.sf_names = refs.get("sf_names", [])
    doc.urls = list(dict.fromkeys(rel_urls + refs.get("urls", [])))
    return doc


# --------------------------------------------------------------------------- #
# Excel (.xlsx / .xlsm) — parsed shapes
# --------------------------------------------------------------------------- #
@dataclass
class SheetTable:
    name: str                                       # workbook-unique displayName
    columns: list = field(default_factory=list)     # DECLARED header names (T1)


@dataclass
class Sheet:
    name: str
    row_count: int = 0                              # used range's last row (0 = empty)
    col_count: int = 0                              # used range's last column
    columns: list = field(default_factory=list)     # heuristic header names (T2)
    tables: list = field(default_factory=list)      # list[SheetTable] (T1)


@dataclass
class XlsxDoc:
    file_id: str = ""
    structure: str = "none"     # declared (any Table part) | heuristic | none
    title: str = ""             # docProps dc:title (never the author)
    modified: str = ""          # docProps dcterms:modified (ISO string)
    has_macros: bool = False    # xl/vbaProject.bin EXISTS; its content is never read
    sheets: list = field(default_factory=list)      # list[Sheet], workbook order
    defined_names: list = field(default_factory=list)   # NAMES only, no refersTo
    urls: list = field(default_factory=list)        # hyperlink rels (+ refs in names)
    jira_keys: list = field(default_factory=list)
    sf_names: list = field(default_factory=list)


_STRING_CELL_TYPES = ("s", "inlineStr")             # plain string cells (header material)
_STRINGISH_TYPES = ("s", "inlineStr", "str")        # + formula string results


def slug(s: str) -> str:
    """Collapse path separators so a sheet/table name is safe inside a
    ``type/name`` id (same contract as ``confluence.parse.slug``). Excel itself
    forbids ``/`` in these names — defense, not an expected path."""
    return (s or "").replace("/", "_").replace("\\", "_").strip()


def _col_index(ref: str) -> int:
    """1-based column number of a cell ref's letter prefix (``D7`` -> 4);
    0 when there is none."""
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n


def _ref_size(ref: str) -> tuple:
    """``(rows, cols)`` extent of a dimension RANGE (``A1:D10`` -> (10, 4));
    ``(0, 0)`` when the ref is not a usable range — a bare ``A1`` is what
    writers stamp on an empty sheet, so it proves nothing."""
    if ":" not in ref:
        return 0, 0
    end = ref.split(":")[-1].strip()
    digits = "".join(ch for ch in end if ch.isdigit())
    if not digits:
        return 0, 0
    return int(digits), _col_index(end)


def _shared_strings(data: bytes) -> list:
    """The shared-strings table, one concatenated text per ``<si>`` (rich-text
    runs joined). These are CELL VALUES — they are looked up only to NAME an
    accepted header row and otherwise never leave the parser."""
    return ["".join((t.text or "") for t in si.iter() if local_name(t.tag) == "t")
            for si in iter_local(_xml_root(data), "si")]


def _cell_string(c, ctype: str, shared: list):
    """``(text, has_value)`` for one cell. Only STRING text is ever returned —
    a numeric/bool/date/formula-result cell yields ``(None, True)``: it can
    gate the type-contrast check, but its value never leaves the parser."""
    if ctype == "inlineStr":
        txt = "".join((t.text or "") for t in c.iter() if local_name(t.tag) == "t").strip()
        return txt, bool(txt)
    v = child(c, "v")
    val = (v.text or "").strip() if v is not None else ""
    if not val:
        return None, False
    if ctype == "s":
        try:
            return (shared[int(val)] or "").strip(), True
        except (ValueError, IndexError):
            return "", True
    return None, True


def _scan_rows(root, shared) -> tuple:
    """One ordered pass over ``sheetData``. Returns ``(max_row, max_col,
    header_row, header_cells, contrast)``: ``header_cells`` is the
    ``[(type, text)]`` of the FIRST value-bearing row (the T2 header
    candidate); ``contrast`` is True when a later value-bearing cell is
    non-string (numeric/bool/date — the data-below-headers signal; a formula's
    string result is string-ish and does not count)."""
    max_row = max_col = header_row = seq = 0
    header = None
    contrast = False
    for row in iter_local(root, "row"):
        try:
            seq = int(row.get("r") or seq + 1)
        except ValueError:
            seq += 1
        cells = []
        for c in children(row, "c"):
            ctype = c.get("t") or "n"
            text, has = _cell_string(c, ctype, shared)
            if not has:
                continue
            cells.append((ctype, text))
            max_row = max(max_row, seq)
            max_col = max(max_col, _col_index(c.get("r") or "") or len(cells))
        if not cells:
            continue
        if header is None:
            header, header_row = cells, seq
        elif not contrast and any(ct not in _STRINGISH_TYPES for ct, _ in cells):
            contrast = True
    return max_row, max_col, header_row, header or [], contrast


def _header_columns(header: list, header_row: int, frozen: int, contrast: bool) -> list:
    """The T2 first-row-as-header gate, confidentiality-guarded: every
    value-bearing cell of the candidate row must be a plain STRING cell
    (accepting a numeric "header" would put data VALUES into the graph),
    non-empty and unique; the row must then be PINNED by a frozen top pane
    covering it or CONFIRMED by type contrast in the rows below. Anything
    less -> no columns attr: dimensions only, honestly flat."""
    if not header or any(ct not in _STRING_CELL_TYPES for ct, _ in header):
        return []
    texts = [(t or "").strip() for _, t in header]
    if not all(texts) or len(set(texts)) != len(texts):
        return []
    if (0 < header_row <= frozen) or contrast:
        return texts
    return []


def _parse_table(data: bytes):
    """A declared Table part (T1): its name + DECLARED column names — the one
    place Excel structure needs no guessing. ``None`` for a nameless part."""
    root = _xml_root(data)
    if local_name(root.tag) != "table":
        return None
    name = (root.get("displayName") or root.get("name") or "").strip()
    if not name:
        return None
    cols = [cn for cn in ((tc.get("name") or "").strip()
                          for tc in iter_local(root, "tableColumn")) if cn]
    return SheetTable(name=name, columns=cols)


def _parse_worksheet(zf, names: set, part: str, sheet: Sheet, shared: list, doc) -> None:
    """Fill one :class:`Sheet` from its worksheet part: declared tables +
    hyperlink URLs (via the sheet rels), extent, and — only on a table-less
    sheet — the gated T2 header heuristic."""
    root = _xml_root(zf.read(part))

    base = posixpath.dirname(part)
    rels_part = posixpath.join(base, "_rels", posixpath.basename(part) + ".rels")
    if rels_part in names:
        for _rid, rtype, target, mode in _relationships(zf.read(rels_part)):
            if not target:
                continue
            if mode == "External":
                if rtype.endswith("/hyperlink"):
                    doc.urls.append(target)
            elif rtype.endswith("/table"):
                tpart = _resolve_part(base, target)
                if tpart in names:
                    table = _parse_table(zf.read(tpart))
                    if table is not None:
                        sheet.tables.append(table)

    # frozen top rows — a layout near-declaration that row 1..N are headers
    frozen = 0
    for pane in iter_local(root, "pane"):
        if (pane.get("state") or "") in ("frozen", "frozenSplit"):
            try:
                frozen = max(frozen, int(float(pane.get("ySplit") or 0)))
            except ValueError:
                pass

    max_row, max_col, header_row, header, contrast = _scan_rows(root, shared)

    # extent: the dimension range when it is a real range, else computed
    dim = next(iter(iter_local(root, "dimension")), None)
    rows, cols = _ref_size(dim.get("ref") or "") if dim is not None else (0, 0)
    sheet.row_count = rows or max_row
    sheet.col_count = cols or max_col

    # T2 only where this sheet declares zero tables — a declared table IS the
    # sheet's structure, a guessed header next to it would just shadow it
    if not sheet.tables:
        sheet.columns = _header_columns(header, header_row, frozen, contrast)


def parse_xlsx(path) -> XlsxDoc:
    """Parse one ``.xlsx`` / ``.xlsm`` into an :class:`XlsxDoc` (raises on a
    corrupt zip or malformed XML — the build records it in ``errors``).

    NAMES ONLY leave this parser: sheet / table / column / defined names. Cell
    values, formula bodies (``<f>``, ``refersTo``) and macro content are never
    read into the result — the raw file keeps them."""
    data = Path(path).read_bytes()
    doc = XlsxDoc(file_id=file_id(data))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        wb = _xml_root(zf.read("xl/workbook.xml"))
        doc.has_macros = "xl/vbaProject.bin" in names           # presence only
        if "docProps/core.xml" in names:
            _core_props(zf.read("docProps/core.xml"), doc)

        # defined NAMES only (their refersTo ranges/formulas stay behind);
        # _xlnm.* built-ins (print areas, filter databases) are noise
        doc.defined_names = list(dict.fromkeys(
            nm for nm in ((dn.get("name") or "").strip()
                          for dn in iter_local(wb, "definedName"))
            if nm and not nm.startswith("_xlnm")))

        sheet_parts = {}
        if "xl/_rels/workbook.xml.rels" in names:
            sheet_parts = {
                rid: _resolve_part("xl", target)
                for rid, rtype, target, mode
                in _relationships(zf.read("xl/_rels/workbook.xml.rels"))
                if rtype.endswith("/worksheet") and mode != "External" and target}
        shared = (_shared_strings(zf.read("xl/sharedStrings.xml"))
                  if "xl/sharedStrings.xml" in names else [])

        position = 0
        for sh in iter_local(wb, "sheet"):
            name = (sh.get("name") or "").strip()
            if not name:
                continue
            position += 1
            sheet = Sheet(name=name)
            doc.sheets.append(sheet)
            part = sheet_parts.get(_attr(sh, "id")) or f"xl/worksheets/sheet{position}.xml"
            if part in names:                # listed-but-absent keeps the name-only node
                _parse_worksheet(zf, names, part, sheet, shared, doc)

    if any(s.tables for s in doc.sheets):
        doc.structure = "declared"
    elif any(s.columns for s in doc.sheets):
        doc.structure = "heuristic"

    # detected refs scan ONLY what the graph already captures (sheet / table /
    # column / defined names + the title) — never raw cell values, so a value
    # can never ride into the graph inside a matched ref
    captured = [s.name for s in doc.sheets] + list(doc.defined_names)
    for s in doc.sheets:
        captured.extend(s.columns)
        for t in s.tables:
            captured.append(t.name)
            captured.extend(t.columns)
    if doc.title:
        captured.append(doc.title)
    refs = detect_refs("\n".join(captured))
    doc.jira_keys = refs.get("jira_keys", [])
    doc.sf_names = refs.get("sf_names", [])
    doc.urls = list(dict.fromkeys(doc.urls + refs.get("urls", [])))
    return doc
