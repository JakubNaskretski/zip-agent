"""Office digest (graph-builder-backed adapter) — synthetic document uploads.

Fixtures are minimal OOXML zips authored in-test with stdlib ``zipfile``
(fictional Acme / MeterPoint content, Polish strings for unicode coverage):
a Word spec with declared headings and an Excel mapping sheet with a heuristic
header row. They cover the three-artifact contract (raw bytes KU + plain-text
sidecar + redacted structure graph), the hard prose rule (entities ALWAYS
empty), idempotent re-ingest, FTS over the sidecar, and bad-file surfacing.
"""
import hashlib
import json
import zipfile

from librarian import Librarian, Store, rebuild_indexes, retrieve
from librarian.digest import office

# --------------------------------------------------------------------------- #
# fixture builders (style of the engine's docx/xlsx extractor tests)
# --------------------------------------------------------------------------- #
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
SS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PK = "http://schemas.openxmlformats.org/package/2006/relationships"

DOCX_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType='
    '"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
XLSX_CT = DOCX_CT.replace(
    '<Override PartName="/word/document.xml" ContentType='
    '"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>',
    '<Override PartName="/xl/workbook.xml" ContentType='
    '"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
)


def _para(text, style=None):
    s = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{s}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def _docx(path, *paras):
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document {W}><w:body>{"".join(paras)}</w:body></w:document>')
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", DOCX_CT)
        z.writestr("word/document.xml", document)
    return path


def _xlsx(path):
    """One sheet 'Dane' with a string header row (Polish) over a numeric data
    row — the engine's gated T2 heuristic accepts it via type contrast."""
    workbook = (f'<workbook xmlns="{SS}" xmlns:r="{RNS}">'
                '<sheets><sheet name="Dane" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = (f'<Relationships xmlns="{PK}">'
            f'<Relationship Id="rId1" Type="{RNS}/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>")
    sst = (f'<sst xmlns="{SS}" count="3" uniqueCount="3">'
           "<si><t>Punkt pomiarowy</t></si><si><t>Wartość nominalna</t></si>"
           "<si><t>MP-A1</t></si></sst>")
    ws = (f'<worksheet xmlns="{SS}"><dimension ref="A1:B2"/><sheetData>'
          '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
          '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>42.5</v></c></row>'
          "</sheetData></worksheet>")
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", XLSX_CT)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/worksheets/sheet1.xml", ws)
        z.writestr("xl/sharedStrings.xml", sst)
    return path


DOCX_REL = "specs/Acme Integration Spec.docx"
XLSX_REL = "mapping/dane.xlsx"


def make_docs_dir(root):
    docs = root / "docs-upload"
    _docx(docs / "specs" / "Acme Integration Spec.docx",
          _para("Overview", style="Heading1"),
          _para("Acme telemetria platform intro, see ACME-101."),
          _para("Punkt pomiarowy", style="Heading2"),
          _para("Szczegóły konfiguracji MeterPoint__c."))
    _xlsx(docs / "mapping" / "dane.xlsx")
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "notes.txt").write_text("a loose file no office extractor handles", "utf-8")
    return docs


def _fid(path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# parse
# --------------------------------------------------------------------------- #
def test_parse_documents_and_summary(tmp_path):
    d = office.parse_office(make_docs_dir(tmp_path))
    assert {doc.rel for doc in d.documents} == {DOCX_REL, XLSX_REL}     # notes.txt skipped
    assert d.errors == [] and d.unresolved == [] and d.skipped == []
    by_rel = {doc.rel: doc for doc in d.documents}
    assert by_rel[DOCX_REL].doc_type == "docx" and by_rel[DOCX_REL].structure == "declared"
    assert by_rel[XLSX_REL].doc_type == "xlsx" and by_rel[XLSX_REL].structure == "heuristic"
    s = d.summary()
    assert s["documents"] == 2 and s["doc_types"] == {"docx": 1, "xlsx": 1}
    assert s["with_text"] == 2
    # docx: docfile + 2 sections; xlsx: docfile + sheet
    assert s["nodes"] == 5
    # docx: contains #1, #2 child-of #1; xlsx: docfile contains sheet
    assert s["edges"] == 3


def test_parse_extracts_sidecar_text(tmp_path):
    d = office.parse_office(make_docs_dir(tmp_path))
    by_rel = {doc.rel: doc for doc in d.documents}
    word = by_rel[DOCX_REL].text
    assert "Punkt pomiarowy" in word                       # heading title
    assert "Szczegóły konfiguracji MeterPoint__c." in word  # section body text
    sheet = by_rel[XLSX_REL].text
    assert "Sheet: Dane" in sheet
    assert "Punkt pomiarowy, Wartość nominalna" in sheet   # header names only
    assert "42.5" not in sheet and "MP-A1" not in sheet    # cell VALUES never leave the raw file


# --------------------------------------------------------------------------- #
# ingest — the three artifacts
# --------------------------------------------------------------------------- #
def test_ingest_creates_kus_sidecar_and_graph(tmp_path):
    lib = Librarian(Store(tmp_path / "mem"))
    docs = make_docs_dir(tmp_path)
    rep, d = office.ingest_office(lib, docs, "dev", "ingest sample office documents")
    assert rep.ok
    ku = lib.get(f"docs:{DOCX_REL}")
    assert ku is not None and ku.tier == "raw" and ku.kind == "source-record"
    assert ku.title == "Acme Integration Spec.docx"
    assert ku.path == f"kb/raw/docs/{DOCX_REL}"
    assert ku.provenance == {
        "doc_type": "docx", "sha12": _fid(docs / DOCX_REL),
        "structure": "declared", "source_path": DOCX_REL,
    }
    side = lib.get(f"docs:{DOCX_REL}#text")
    assert side is not None and side.tier == "raw" and side.kind == "source-record"
    assert side.path == f"kb/raw/docs/{DOCX_REL}.txt"
    assert side.links == [{"kind": "derived-from", "to": f"docs:{DOCX_REL}"}]
    assert lib.get(f"docs:{XLSX_REL}") is not None
    assert lib.get(f"docs:{XLSX_REL}#text") is not None
    gku = lib.get(office.GRAPH_ID)
    assert gku is not None and gku.tier == "structured" and gku.kind == "graph"
    g = office.load_graph(lib)
    assert len(g["nodes"]) == 5 and len(g["edges"]) == 3
    fid = _fid(docs / DOCX_REL)
    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    assert (f"docfile/{fid}", "contains", f"docsection/{fid}#1") in edges
    assert (f"docsection/{fid}#2", "child-of", f"docsection/{fid}#1") in edges


def test_entities_always_empty(tmp_path):
    """HARD RULE: document KUs carry NO entities — never filenames, titles,
    headings or column names. Prose is found via FTS, not the bridge."""
    lib = Librarian(Store(tmp_path / "mem"))
    office.ingest_office(lib, make_docs_dir(tmp_path), "dev",
                         "ingest sample office documents")
    docs_kus = [ku for ku in lib.manifest.all() if ku.source == "docs"]
    assert len(docs_kus) == 5                              # 2 raw + 2 sidecars + graph
    assert all(ku.entities == [] for ku in docs_kus)


def test_body_roundtrip_returns_original_bytes(tmp_path):
    """The raw KU body IS the uploaded file: ``read_body`` returns the exact
    bytes (re-openable, re-parseable); the sidecar carries the plaintext."""
    lib = Librarian(Store(tmp_path / "mem"))
    docs = make_docs_dir(tmp_path)
    office.ingest_office(lib, docs, "dev", "ingest sample office documents")
    original = (docs / DOCX_REL).read_bytes()
    assert lib.read_body(f"docs:{DOCX_REL}") == original
    with zipfile.ZipFile(lib.store.abspath(f"kb/raw/docs/{DOCX_REL}")) as z:
        assert "word/document.xml" in z.namelist()         # still a valid docx
    text = lib.read_body(f"docs:{DOCX_REL}#text").decode("utf-8")
    assert "Szczegóły konfiguracji MeterPoint__c." in text


def test_graph_body_redacts_section_text(tmp_path):
    """Section text lives in the sidecars; the stored graph JSON must carry no
    inline ``text`` attr — and only intra-docs edges (no cross-domain join)."""
    lib = Librarian(Store(tmp_path / "mem"))
    office.ingest_office(lib, make_docs_dir(tmp_path), "dev",
                         "ingest sample office documents")
    stored = json.loads(lib.read_body(office.GRAPH_ID))
    assert all("text" not in n for n in stored["nodes"])
    redacted = [n["id"] for n in stored["nodes"] if n.get("text_redacted")]
    assert any(nid.startswith("docsection/") for nid in redacted)
    assert {e["type"] for e in stored["edges"]} <= {"contains", "child-of"}
    # detected refs stay attrs, never edges or entities
    docfile = next(n for n in stored["nodes"] if n["label"].endswith(".docx"))
    assert docfile["jira_keys"] == ["ACME-101"] and docfile["sf_names"] == ["MeterPoint__c"]


def test_reingest_unchanged_is_noop(tmp_path):
    lib = Librarian(Store(tmp_path / "mem"))
    docs = make_docs_dir(tmp_path)
    office.ingest_office(lib, docs, "dev", "ingest sample office documents")
    gen = lib.manifest.generation
    rep, _ = office.ingest_office(lib, docs, "dev", "re-ingest identical documents")
    assert rep.unchanged and lib.manifest.generation == gen


def test_fts_finds_document_by_body_word_never_the_bridge(tmp_path):
    """Document prose is reached via full-text search over the sidecar — the
    entity bridge must contain no doc titles, headings or filenames."""
    lib = Librarian(Store(tmp_path / "mem"))
    office.ingest_office(lib, make_docs_dir(tmp_path), "dev",
                         "ingest sample office documents")
    rebuild_indexes(lib, "dev", "rebuild indexes after docs digest")
    con = retrieve.open_index(lib)
    hits = {h["ku_id"] for h in retrieve.search(con, "telemetria", source="docs")}
    assert f"docs:{DOCX_REL}#text" in hits                 # a Word body word
    hits = {h["ku_id"] for h in retrieve.search(con, "Wartość nominalna")}
    assert f"docs:{XLSX_REL}#text" in hits                 # an Excel column name
    for name in ("Acme Integration Spec.docx", "Overview", "Punkt pomiarowy", "Dane"):
        assert retrieve.find_entity(con, name) == []       # prose stays out of the bridge


def test_bad_file_recorded_never_raises(tmp_path):
    docs = make_docs_dir(tmp_path)
    (docs / "broken.docx").write_bytes(b"this is not a zip archive at all")
    d = office.parse_office(docs)
    assert len(d.errors) == 1 and "broken.docx" in d.skipped[0]
    assert {doc.rel for doc in d.documents} == {DOCX_REL, XLSX_REL}   # good files survive
    lib = Librarian(Store(tmp_path / "mem"))
    rep, _ = office.ingest_office(lib, docs, "dev",
                                  "ingest documents with one broken file")
    assert rep.ok and lib.get(f"docs:{XLSX_REL}") is not None
    assert lib.get("docs:broken.docx") is None             # no KU minted for the bad file
