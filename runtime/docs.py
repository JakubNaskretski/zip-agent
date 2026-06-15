"""Read office-document content on demand — workbook cells and text sidecars.

The office digest keeps only sheet/table/column **names** in the graph (a
confidentiality rule) and writes a plain-text sidecar for prose. But an RFP
workbook's substance — sizing numbers, assumptions, mapping, Q&A answers — lives
in its **cells**, which are deliberately not in any index. :func:`read_workbook`
re-opens the stored ``.xlsx`` bytes from the working folder / zip and returns the
actual cell contents.

Strictly read-only: it parses OOXML in memory and returns plain data — it writes
nothing. The parser is stdlib-only and hardened against entity-expansion / XXE
(it refuses any part declaring a DTD), so a client-supplied workbook is safe to
open. This is the lightweight home of the reader — it sources bytes from a
:class:`~runtime.storage.Workspace` and pulls in nothing heavy.
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

from . import layout

# OOXML SpreadsheetML namespaces
_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_T = "{%s}t" % _NS["m"]


def _fromstring(data: bytes):
    """Parse an OOXML part, refusing any DTD/DOCTYPE — which closes
    entity-expansion (billion-laughs) and, with stdlib ElementTree not resolving
    external entities, XXE. Real OOXML never declares one, so a whole-part scan
    has no false positives."""
    if re.search(rb"<!doctype", data, re.IGNORECASE):
        raise ValueError("XML part declares a DTD/DOCTYPE — refused (not valid OOXML)")
    return ET.fromstring(data)


def _col_index(ref: str) -> int:
    m = re.match(r"[A-Za-z]+", ref or "")
    if not m:
        return 0
    n = 0
    for ch in m.group(0).upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _shared_strings(zf: zipfile.ZipFile) -> list:
    try:
        root = _fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(t.text or "" for t in si.iter(_T))
            for si in root.findall("m:si", _NS)]


def _sheet_targets(zf: zipfile.ZipFile) -> list:
    try:
        wb = _fromstring(zf.read("xl/workbook.xml"))
        rels = _fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    except KeyError as e:
        raise ValueError(f"workbook missing required part {e} — not a valid .xlsx")
    rid_to_target = {rel.get("Id"): rel.get("Target")
                     for rel in rels.findall("pr:Relationship", _NS)}
    out = []
    for sh in wb.findall("m:sheets/m:sheet", _NS):
        name = sh.get("name") or ""
        target = rid_to_target.get(sh.get("{%s}id" % _NS["r"]), "")
        if not target:
            continue
        if target.startswith("/"):
            target = target[1:]
        elif not target.startswith("xl/"):
            target = "xl/" + target
        out.append((name, target))
    return out


def _cell_value(c, shared: list) -> str:
    t = c.get("t")
    if t == "s":
        v = c.find("m:v", _NS)
        if v is not None and v.text is not None:
            i = int(v.text)
            return shared[i] if 0 <= i < len(shared) else ""
        return ""
    if t == "inlineStr":
        is_ = c.find("m:is", _NS)
        return "".join(tt.text or "" for tt in is_.iter(_T)) if is_ is not None else ""
    v = c.find("m:v", _NS)
    return v.text if (v is not None and v.text is not None) else ""


def _read_sheet(zf: zipfile.ZipFile, target: str, shared: list, max_rows) -> list:
    try:
        root = _fromstring(zf.read(target))
    except KeyError:
        return []
    rows = []
    for i, row in enumerate(root.findall(".//m:sheetData/m:row", _NS)):
        if max_rows is not None and i >= max_rows:
            break
        placed, cursor = [], 0
        for c in row.findall("m:c", _NS):
            ref = c.get("r", "")
            ci = _col_index(ref) if re.match(r"[A-Za-z]", ref) else cursor
            placed.append((ci, _cell_value(c, shared)))
            cursor = ci + 1
        if not placed:
            rows.append([])
            continue
        width = max(ci for ci, _ in placed) + 1
        dense = [""] * width
        for ci, val in placed:
            dense[ci] = val
        rows.append(dense)
    return rows


def _raw_docs_path(rel: str) -> str:
    """Map a doc rel path (or a ``docs:<rel>`` id) to its raw file path."""
    if rel.startswith("docs:"):
        rel = rel.split(":", 1)[1]
    return f"{layout.raw_dir('docs')}/{rel}"


def read_workbook(ws, rel: str, *, sheet=None, max_rows=None) -> dict:
    """Cell contents of a stored workbook: ``{sheet_name: [[cell, ...], ...]}``.

    ``rel`` is the document's relative path (or its ``docs:<rel>`` id). ``sheet``
    limits to one sheet; ``max_rows`` caps rows per sheet (keep stdout bounded).
    Raises ``ValueError`` if the file is not an OOXML workbook."""
    body = ws.read_bytes(_raw_docs_path(rel))
    if not body or body[:4] != b"PK\x03\x04":
        raise ValueError(f"{rel!r} is not an OOXML workbook (need .xlsx/.xlsm)")
    out: dict = {}
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        shared = _shared_strings(zf)
        for name, target in _sheet_targets(zf):
            if sheet is not None and name != sheet:
                continue
            out[name] = _read_sheet(zf, target, shared, max_rows)
    return out


def read_table(ws, rel: str, sheet: str, *, header_row: int = 0) -> dict:
    """A tidy-table view of one sheet: ``{"headers": [...], "rows": [[...], ...]}``."""
    rows = read_workbook(ws, rel, sheet=sheet).get(sheet, [])
    if not rows or header_row < 0 or header_row >= len(rows):
        return {"headers": [], "rows": []}
    return {"headers": rows[header_row], "rows": rows[header_row + 1:]}


def doc_text(ws, rel: str) -> str:
    """The plain-text sidecar for a document (``kb/raw/docs/<rel>.txt``), or ''.

    This is the searchable prose the office digest extracted (Word section text,
    Excel sheet/table/column names, PowerPoint titles/body/notes)."""
    if rel.startswith("docs:"):
        rel = rel.split(":", 1)[1]
    path = f"{layout.raw_dir('docs')}/{rel}.txt"
    return ws.read_text(path) if ws.exists(path) else ""
