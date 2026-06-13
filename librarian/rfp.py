"""RFP-support helpers (read-only) — used by the ``rfp`` profile.

The office digest deliberately keeps only sheet / table / column **names** in the
searchable sidecar; the actual **cell values** (sizing numbers, assumptions,
technical mapping, Q&A answers) never enter the search surface — a confidentiality
rule in the extractor. But for RFP-pursuit work the team's richest document is
usually a big workbook whose substance lives in those cells.

``read_workbook`` re-opens the stored workbook bytes (the media-stripped working
copy the digest keeps in full) and returns the actual cell contents so the agent
can read and reason over them. It is strictly READ-ONLY: it opens the raw KU body,
parses the OOXML in memory, and returns plain data — it writes nothing, commits
nothing, and adds nothing to the entity bridge.

    from librarian import rfp
    rfp.read_workbook(lib, "docs:pursuit/sizing.xlsx")          # all sheets
    rfp.read_table(lib, "docs:pursuit/sizing.xlsx", "Sizing")   # one sheet as headers+rows
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

# OOXML SpreadsheetML namespaces
_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_T = "{%s}t" % _NS["m"]


def _fromstring(data: bytes):
    """Parse an OOXML part, hardened against XXE / billion-laughs entity attacks.

    Office files can be client-supplied, so the XML is semi-untrusted. This engine
    is stdlib-only and offline (``defusedxml`` is not a shipped runtime dependency),
    so we harden the stdlib parser the proportionate way: legitimate OOXML parts
    never declare a DTD, and every entity-expansion attack REQUIRES a DTD/DOCTYPE —
    so we refuse any part that declares one. With no DOCTYPE there are no custom or
    external entities to expand, which closes both billion-laughs (entity-expansion
    DoS) and external-entity (file/URL) reads while staying dependency-free.
    """
    if b"<!doctype" in data[:4096].lower():
        raise ValueError("XML part declares a DTD/DOCTYPE — refused (not valid "
                         "OOXML; blocks entity-expansion / XXE attacks)")
    return ET.fromstring(data)


def _col_index(ref: str) -> int:
    """Column letters of a cell ref ('B7' -> 1, 0-based). Defaults to 0."""
    m = re.match(r"[A-Za-z]+", ref or "")
    if not m:
        return 0
    n = 0
    for ch in m.group(0).upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _shared_strings(zf: zipfile.ZipFile) -> list:
    """The workbook's shared-string table; cells with t='s' index into this."""
    try:
        root = _fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    out = []
    for si in root.findall("m:si", _NS):
        out.append("".join(t.text or "" for t in si.iter(_T)))   # <t> or split <r><t>
    return out


def _sheet_targets(zf: zipfile.ZipFile) -> list:
    """[(sheet_name, part_path)] in workbook order, resolved via the rels."""
    wb = _fromstring(zf.read("xl/workbook.xml"))
    rels = _fromstring(zf.read("xl/_rels/workbook.xml.rels"))
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
    if t == "s":                                   # shared string (an index)
        v = c.find("m:v", _NS)
        if v is not None and v.text is not None:
            i = int(v.text)
            return shared[i] if 0 <= i < len(shared) else ""
        return ""
    if t == "inlineStr":                           # inline string
        is_ = c.find("m:is", _NS)
        return "".join(tt.text or "" for tt in is_.iter(_T)) if is_ is not None else ""
    v = c.find("m:v", _NS)                          # number / bool / formula cache
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
        placed = [(_col_index(c.get("r", "")), _cell_value(c, shared))
                  for c in row.findall("m:c", _NS)]
        if not placed:
            rows.append([])
            continue
        width = max(ci for ci, _ in placed) + 1
        dense = [""] * width
        for ci, val in placed:
            dense[ci] = val
        rows.append(dense)
    return rows


def read_workbook(lib, ku_id, *, sheet=None, max_rows=None) -> dict:
    """Read the actual cell contents of a stored Excel KU.

    Returns ``{sheet_name: [[cell, cell, ...], ...]}`` (rows of string cell
    values, shared strings resolved, dense by column). ``sheet`` limits to one
    sheet by name; ``max_rows`` caps rows per sheet (keep stdout bounded).

    Raises ``ValueError`` if the KU body is not an OOXML zip (e.g. a PDF, or a
    legacy binary ``.xls``) — those are not readable cell-wise here.
    """
    body = lib.read_body(ku_id)
    if not body or body[:4] != b"PK\x03\x04":
        raise ValueError(
            f"{ku_id!r} is not an OOXML workbook (need .xlsx/.xlsm; "
            "PDF/legacy-.xls bytes cannot be read cell-wise)")
    out: dict = {}
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        shared = _shared_strings(zf)
        for name, target in _sheet_targets(zf):
            if sheet is not None and name != sheet:
                continue
            out[name] = _read_sheet(zf, target, shared, max_rows)
    return out


def read_table(lib, ku_id, sheet, *, header_row=0) -> dict:
    """A tidy-table view of one sheet: ``{"headers": [...], "rows": [[...], ...]}``.

    Treats ``header_row`` (0-based, default the first row) as column headers and
    everything after it as data rows. For a clean requirements/sizing matrix this
    is the convenient view; for a messy sheet, use :func:`read_workbook` and
    interpret the grid directly.
    """
    rows = read_workbook(lib, ku_id, sheet=sheet).get(sheet, [])
    if not rows or header_row >= len(rows):
        return {"headers": [], "rows": []}
    return {"headers": rows[header_row], "rows": rows[header_row + 1:]}
