"""rfp.read_workbook / read_table — read the cell values the office digest keeps
out of the search surface, plus the XXE/DTD hardening guard."""
import io
import zipfile

import pytest

from librarian import rfp


# --------------------------------------------------------------------------- #
# a minimal, valid .xlsx built by hand (no third-party deps)
# --------------------------------------------------------------------------- #
def _xlsx(grid, *, sheet_name="Sizing", doctype_in_sheet=False) -> bytes:
    """grid: list of rows, each a list of (value, is_number). Strings go through
    the shared-string table (t='s'); numbers are bare <v>."""
    strings: list = []

    def sidx(s):
        if s not in strings:
            strings.append(s)
        return strings.index(s)

    def ref(ci, ri):
        s, n = "", ci + 1
        while n > 0:
            n, rem = divmod(n - 1, 26)
            s = chr(65 + rem) + s
        return f"{s}{ri + 1}"

    rows_xml = []
    for ri, row in enumerate(grid):
        cells = []
        for ci, (val, is_num) in enumerate(row):
            if is_num:
                cells.append(f'<c r="{ref(ci, ri)}"><v>{val}</v></c>')
            else:
                cells.append(f'<c r="{ref(ci, ri)}" t="s"><v>{sidx(val)}</v></c>')
        rows_xml.append(f'<row r="{ri + 1}">{"".join(cells)}</row>')

    doctype = ('<!DOCTYPE x [<!ENTITY lol "lol">]>' if doctype_in_sheet else "")
    sheet = (f'<?xml version="1.0"?>{doctype}'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<sheetData>{"".join(rows_xml)}</sheetData></worksheet>')
    sst = ('<?xml version="1.0"?><sst '
           'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
    workbook = ('<?xml version="1.0"?><workbook '
                'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wbrels = ('<?xml version="1.0"?><Relationships '
              'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
              '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
              '</Relationships>')
    ct = ('<?xml version="1.0"?><Types '
          'xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/></Types>')
    rels = ('<?xml version="1.0"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wbrels)
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


class _StubLib:
    """read_workbook only needs lib.read_body(ku_id)."""
    def __init__(self, body):
        self._body = body

    def read_body(self, ku_id):
        return self._body


def test_read_workbook_returns_resolved_cell_values():
    body = _xlsx([[("Item", False), ("Licences", False)],
                  [("Service Cloud", False), ("250", True)]])
    wb = rfp.read_workbook(_StubLib(body), "docs:pursuit/sizing.xlsx")
    assert "Sizing" in wb
    assert wb["Sizing"][0] == ["Item", "Licences"]       # shared strings resolved
    assert wb["Sizing"][1] == ["Service Cloud", "250"]   # number cell read too


def test_read_table_splits_headers_and_rows():
    body = _xlsx([[("Item", False), ("Licences", False)],
                  [("Service Cloud", False), ("250", True)],
                  [("Data Cloud", False), ("1", True)]])
    t = rfp.read_table(_StubLib(body), "docs:pursuit/sizing.xlsx", "Sizing")
    assert t["headers"] == ["Item", "Licences"]
    assert t["rows"] == [["Service Cloud", "250"], ["Data Cloud", "1"]]


def test_sheet_filter_and_missing_sheet():
    body = _xlsx([[("a", False)]], sheet_name="Assumptions")
    assert list(rfp.read_workbook(_StubLib(body), "x", sheet="Assumptions")) == ["Assumptions"]
    assert rfp.read_workbook(_StubLib(body), "x", sheet="Nope") == {}


def test_non_ooxml_body_is_rejected():
    with pytest.raises(ValueError):
        rfp.read_workbook(_StubLib(b"%PDF-1.7\n..."), "docs:reqs.pdf")


def test_dtd_part_is_refused_xxe_guard():
    # a workbook whose worksheet smuggles a DTD/entity must be refused, not parsed
    body = _xlsx([[("a", False)]], doctype_in_sheet=True)
    with pytest.raises(ValueError):
        rfp.read_workbook(_StubLib(body), "docs:evil.xlsx")


def test_fromstring_refuses_late_doctype():
    # the guard must scan the WHOLE part — a DOCTYPE pushed past 4 KB (behind a big
    # leading comment) must still be refused, not slipped through.
    payload = (b'<?xml version="1.0"?><!-- ' + b'x' * 5000 + b' -->'
               b'<!DOCTYPE r [<!ENTITY a "b">]><r/>')
    with pytest.raises(ValueError):
        rfp._fromstring(payload)
    assert rfp._fromstring(b'<?xml version="1.0"?><r><c>ok</c></r>') is not None


def _assemble_xlsx(sheet_data_inner, sst_inner="", sheet_name="S") -> bytes:
    """Build a minimal .xlsx from raw <sheetData> inner XML + raw <sst> inner XML —
    for the cell encodings the grid helper above doesn't express (inlineStr, rich text)."""
    M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    PR = "http://schemas.openxmlformats.org/package/2006/relationships"
    sheet = (f'<?xml version="1.0"?><worksheet xmlns="{M}">'
             f'<sheetData>{sheet_data_inner}</sheetData></worksheet>')
    sst = f'<?xml version="1.0"?><sst xmlns="{M}">{sst_inner}</sst>'
    workbook = (f'<?xml version="1.0"?><workbook xmlns="{M}" xmlns:r="{R}">'
                f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wbrels = (f'<?xml version="1.0"?><Relationships xmlns="{PR}">'
              f'<Relationship Id="rId1" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/>'
              f'<Relationship Id="rId2" Type="{R}/sharedStrings" Target="sharedStrings.xml"/>'
              '</Relationships>')
    ct = ('<?xml version="1.0"?><Types '
          'xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/></Types>')
    rels = (f'<?xml version="1.0"?><Relationships xmlns="{PR}">'
            f'<Relationship Id="rId1" Type="{R}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wbrels)
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def test_inline_string_cell():
    body = _assemble_xlsx('<row r="1"><c r="A1" t="inlineStr"><is><t>hello</t></is></c></row>')
    assert rfp.read_workbook(_StubLib(body), "x")["S"][0] == ["hello"]


def test_rich_text_shared_string():
    body = _assemble_xlsx('<row r="1"><c r="A1" t="s"><v>0</v></c></row>',
                          sst_inner="<si><r><t>foo</t></r><r><t>bar</t></r></si>")
    assert rfp.read_workbook(_StubLib(body), "x")["S"][0] == ["foobar"]


def test_max_rows_caps_rows():
    rows = "".join(f'<row r="{i+1}"><c r="A{i+1}" t="inlineStr"><is><t>r{i}</t></is></c></row>'
                   for i in range(5))
    assert len(rfp.read_workbook(_StubLib(_assemble_xlsx(rows)), "x", max_rows=2)["S"]) == 2


def test_read_table_header_row_offset_and_bad_index():
    body = _assemble_xlsx(
        '<row r="1"><c r="A1" t="inlineStr"><is><t>title</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>H</t></is></c></row>'
        '<row r="3"><c r="A3" t="inlineStr"><is><t>v</t></is></c></row>')
    t = rfp.read_table(_StubLib(body), "x", "S", header_row=1)
    assert t["headers"] == ["H"] and t["rows"] == [["v"]]
    # negative / out-of-range header_row -> empty, never a nonsensical split
    assert rfp.read_table(_StubLib(body), "x", "S", header_row=-1) == {"headers": [], "rows": []}
