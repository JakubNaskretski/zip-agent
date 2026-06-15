"""Office digest (graph-builder-backed adapter) — synthetic document uploads.

Fixtures are minimal OOXML zips authored in-test with stdlib ``zipfile``
(fictional Acme / MeterPoint content, Polish strings for unicode coverage):
a Word spec with declared headings, an Excel mapping sheet with a heuristic
header row, and a PowerPoint deck with slides, speaker notes and a chart. They
cover the three-artifact contract (raw bytes KU + plain-text sidecar + redacted
structure graph), the hard prose rule (entities ALWAYS empty), idempotent
re-ingest, FTS over the sidecar, and bad-file surfacing.
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


def test_parse_accepts_a_single_file(tmp_path):
    """``docs_dir`` may point straight at one document, not only a folder — the
    agent that hands ``parse_office`` a lone ``.docx``/``.pptx`` must not get a
    silent empty digest. ``rel`` is taken relative to the file's parent."""
    doc = _docx(tmp_path / "Acme Integration Spec.docx",
                _para("Overview", style="Heading1"),
                _para("Szczegóły konfiguracji MeterPoint__c."))
    d = office.parse_office(doc)
    assert [doc_.rel for doc_ in d.documents] == ["Acme Integration Spec.docx"]
    assert d.errors == [] and d.skipped == []
    assert "Szczegóły konfiguracji MeterPoint__c." in d.documents[0].text


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


def test_ingest_accepts_a_single_file(tmp_path):
    """End-to-end single-file ingest: a lone document yields its raw KU +
    sidecar keyed by the bare filename (rel = relative to the parent)."""
    lib = Librarian(Store(tmp_path / "mem"))
    doc = _docx(tmp_path / "Acme Integration Spec.docx",
                _para("Overview", style="Heading1"),
                _para("Szczegóły konfiguracji MeterPoint__c."))
    rep, d = office.ingest_office(lib, doc, "dev", "ingest a single office doc")
    assert rep.ok
    assert [doc_.rel for doc_ in d.documents] == ["Acme Integration Spec.docx"]
    assert lib.get("docs:Acme Integration Spec.docx") is not None
    assert lib.get("docs:Acme Integration Spec.docx#text") is not None


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


# --------------------------------------------------------------------------- #
# PowerPoint (pptx) digest tests
# --------------------------------------------------------------------------- #
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P14_NS = "http://schemas.microsoft.com/office/powerpoint/2010/main"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"

PPTX_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/ppt/presentation.xml" ContentType='
    '"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
    "</Types>"
)


def _pptx_presentation(slide_rids):
    """Minimal ppt/presentation.xml with ordered slide ids."""
    sld_id_els = "".join(
        f'<p:sldId id="{i + 256}" r:id="{rid}"/>'
        for i, rid in enumerate(slide_rids)
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:presentation xmlns:p="{P_NS}" xmlns:r="{RNS}">'
        f'<p:sldIdLst>{sld_id_els}</p:sldIdLst>'
        "</p:presentation>"
    )


def _pptx_prs_rels(*slide_parts):
    entries = "".join(
        f'<Relationship Id="rId{i + 1}" Type="{RNS}/slide" Target="{part}"/>'
        for i, part in enumerate(slide_parts)
    )
    return f'<Relationships xmlns="{PK}">{entries}</Relationships>'


def _pptx_slide(title=None, body_paras=None):
    shapes = ""
    if title:
        shapes += (
            f'<p:sp xmlns:p="{P_NS}" xmlns:a="{A_NS}">'
            f'<p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
            f'<p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p></p:txBody>'
            "</p:sp>"
        )
    if body_paras:
        paras = "".join(
            f'<a:p xmlns:a="{A_NS}"><a:r><a:t>{p}</a:t></a:r></a:p>'
            for p in body_paras
        )
        shapes += (
            f'<p:sp xmlns:p="{P_NS}" xmlns:a="{A_NS}">'
            "<p:nvSpPr><p:nvPr/></p:nvSpPr>"
            f"<p:txBody>{paras}</p:txBody></p:sp>"
        )
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sld xmlns:p="{P_NS}" xmlns:a="{A_NS}">'
        f"<p:cSld><p:spTree>{shapes}</p:spTree></p:cSld></p:sld>"
    )


def _pptx_slide_rels(*rels):
    entries = "".join(
        f'<Relationship Id="{rid}" Type="{RNS}/{rtype}" Target="{target}"/>'
        for rid, rtype, target in rels
    )
    return f'<Relationships xmlns="{PK}">{entries}</Relationships>'


def _pptx_notes(text):
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:notes xmlns:p="{P_NS}" xmlns:a="{A_NS}">'
        f'<p:cSld><p:spTree>'
        f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>'
        f'<p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody>'
        "</p:sp></p:spTree></p:cSld></p:notes>"
    )


def _pptx_chart(title=None, series=None, categories=None, numeric_vals=None):
    title_xml = ""
    if title:
        title_xml = (
            f'<c:title xmlns:c="{C_NS}">'
            f'<c:tx><c:rich><a:p xmlns:a="{A_NS}"><a:r><a:t>{title}</a:t></a:r></a:p>'
            f"</c:rich></c:tx></c:title>"
        )
    series_xml = ""
    for s_name in (series or []):
        cat_xml = ""
        if categories:
            pts = "".join(
                f'<c:pt xmlns:c="{C_NS}" idx="{i}"><c:v>{lbl}</c:v></c:pt>'
                for i, lbl in enumerate(categories)
            )
            cat_xml = (
                f'<c:cat xmlns:c="{C_NS}"><c:strRef><c:strCache>{pts}</c:strCache>'
                "</c:strRef></c:cat>"
            )
        val_xml = ""
        if numeric_vals:
            npts = "".join(
                f'<c:pt xmlns:c="{C_NS}" idx="{i}"><c:v>{v}</c:v></c:pt>'
                for i, v in enumerate(numeric_vals)
            )
            val_xml = (
                f'<c:val xmlns:c="{C_NS}"><c:numRef><c:numCache>{npts}</c:numCache>'
                "</c:numRef></c:val>"
            )
        series_xml += (
            f'<c:ser xmlns:c="{C_NS}">'
            f'<c:tx><c:strRef><c:strCache>'
            f'<c:pt xmlns:c="{C_NS}" idx="0"><c:v>{s_name}</c:v></c:pt>'
            f"</c:strCache></c:strRef></c:tx>"
            f"{cat_xml}{val_xml}</c:ser>"
        )
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<c:chartSpace xmlns:c="{C_NS}">'
        f'<c:chart>{title_xml}<c:plotArea>{series_xml}</c:plotArea></c:chart>'
        "</c:chartSpace>"
    )


PPTX_REL = "slides/Acme Overview.pptx"


def make_pptx(root):
    """A two-slide deck: slide 1 has title+body text; slide 2 has speaker notes
    and a chart with series/categories but also numeric values (excluded)."""
    deck_path = root / "slides" / "Acme Overview.pptx"
    deck_path.parent.mkdir(parents=True, exist_ok=True)

    slide2_chart = _pptx_chart(
        title="Pipeline Traffic",
        series=["Inbound", "Outbound"],
        categories=["Jan", "Feb"],
        numeric_vals=[1000, 2000],      # numeric values MUST NOT appear in sidecar
    )
    parts = {
        "ppt/presentation.xml": _pptx_presentation(["rId1", "rId2"]),
        "ppt/_rels/presentation.xml.rels": _pptx_prs_rels(
            "slides/slide1.xml", "slides/slide2.xml"),
        "ppt/slides/slide1.xml": _pptx_slide(
            title="Acme Integration Overview",
            body_paras=["Key components of the Acme telemetry system."]),
        "ppt/slides/slide2.xml": _pptx_slide(title="Results"),
        "ppt/slides/_rels/slide2.xml.rels": _pptx_slide_rels(
            ("rId10", "notesSlide", "../noteSlides/notesSlide2.xml"),
            ("rId20", "chart", "../charts/chart1.xml"),
        ),
        "ppt/noteSlides/notesSlide2.xml": _pptx_notes(
            "Remember: pipeline latency SLA is 200ms."),
        "ppt/charts/chart1.xml": slide2_chart,
    }
    with zipfile.ZipFile(deck_path, "w") as z:
        z.writestr("[Content_Types].xml", PPTX_CT)
        for member, data in parts.items():
            z.writestr(member, data)
    return deck_path


# --------------------------------------------------------------------------- #
# pptx parse
# --------------------------------------------------------------------------- #
def test_pptx_parse_document_recorded(tmp_path):
    docs = tmp_path / "docs"
    make_pptx(docs)
    d = office.parse_office(docs)
    by_rel = {doc.rel: doc for doc in d.documents}
    assert PPTX_REL in by_rel
    rec = by_rel[PPTX_REL]
    assert rec.doc_type == "pptx"
    assert d.errors == [] and d.skipped == []


def test_pptx_doc_types_in_summary(tmp_path):
    docs = tmp_path / "docs"
    make_docs_dir(docs.parent)   # adds docx + xlsx into docs-upload
    make_pptx(docs)
    all_docs_dir = tmp_path / "all"
    all_docs_dir.mkdir()
    import shutil
    shutil.copytree(docs.parent / "docs-upload", all_docs_dir / "word_excel",
                    dirs_exist_ok=True)
    shutil.copytree(docs, all_docs_dir / "pptx_dir", dirs_exist_ok=True)
    d = office.parse_office(all_docs_dir)
    s = d.summary()
    assert "pptx" in s["doc_types"]
    assert s["doc_types"]["pptx"] >= 1


# --------------------------------------------------------------------------- #
# pptx ingest — three artifacts
# --------------------------------------------------------------------------- #
def test_pptx_raw_ku_body_byte_identical(tmp_path):
    """The raw KU body for a pptx file must be byte-identical to the original."""
    docs = tmp_path / "docs"
    deck = make_pptx(docs)
    lib = Librarian(Store(tmp_path / "mem"))
    office.ingest_office(lib, docs, "dev", "ingest pptx presentation")
    original = deck.read_bytes()
    assert lib.read_body(f"docs:{PPTX_REL}") == original


def test_pptx_sidecar_contains_slide_title_and_body(tmp_path):
    docs = tmp_path / "docs"
    make_pptx(docs)
    lib = Librarian(Store(tmp_path / "mem"))
    office.ingest_office(lib, docs, "dev", "ingest pptx presentation")
    text = lib.read_body(f"docs:{PPTX_REL}#text").decode("utf-8")
    # slide 1 title and body text appear in sidecar
    assert "Acme Integration Overview" in text
    assert "Acme telemetry system" in text


def test_pptx_sidecar_contains_speaker_notes(tmp_path):
    docs = tmp_path / "docs"
    make_pptx(docs)
    lib = Librarian(Store(tmp_path / "mem"))
    office.ingest_office(lib, docs, "dev", "ingest pptx presentation")
    text = lib.read_body(f"docs:{PPTX_REL}#text").decode("utf-8")
    assert "pipeline latency SLA" in text


def test_pptx_sidecar_contains_chart_labels_not_numeric_values(tmp_path):
    """Chart series/category labels appear in the sidecar; numeric values do not."""
    docs = tmp_path / "docs"
    make_pptx(docs)
    lib = Librarian(Store(tmp_path / "mem"))
    office.ingest_office(lib, docs, "dev", "ingest pptx presentation")
    text = lib.read_body(f"docs:{PPTX_REL}#text").decode("utf-8")
    assert "Inbound" in text and "Outbound" in text   # series names
    assert "Jan" in text and "Feb" in text             # category labels
    assert "1000" not in text and "2000" not in text   # numeric values excluded


def test_pptx_graph_contains_slide_and_chart_nodes(tmp_path):
    docs = tmp_path / "docs"
    make_pptx(docs)
    lib = Librarian(Store(tmp_path / "mem"))
    office.ingest_office(lib, docs, "dev", "ingest pptx presentation")
    g = office.load_graph(lib)
    node_types = {n["type"] for n in g["nodes"]}
    assert "slide" in node_types
    assert "chart" in node_types


def test_pptx_entities_always_empty(tmp_path):
    """HARD RULE: pptx KUs carry NO entities."""
    docs = tmp_path / "docs"
    make_pptx(docs)
    lib = Librarian(Store(tmp_path / "mem"))
    office.ingest_office(lib, docs, "dev", "ingest pptx presentation")
    pptx_kus = [ku for ku in lib.manifest.all()
                if ku.source == "docs" and "pptx" in ku.id]
    assert len(pptx_kus) >= 1
    assert all(ku.entities == [] for ku in pptx_kus)


# --------------------------------------------------------------------------- #
# media-stripping tests
# --------------------------------------------------------------------------- #
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4096  # 4+ KB fake image bytes
FAKE_JPEG = b"\xff\xd8\xff" + b"\x00" * 4096


def make_pptx_with_media(root):
    """Variant of make_pptx that also embeds a fake image and a thumbnail."""
    deck_path = root / "slides" / "Acme Media Deck.pptx"
    deck_path.parent.mkdir(parents=True, exist_ok=True)
    parts = {
        "[Content_Types].xml": PPTX_CT,
        "ppt/presentation.xml": _pptx_presentation(["rId1"]),
        "ppt/_rels/presentation.xml.rels": _pptx_prs_rels("slides/slide1.xml"),
        "ppt/slides/slide1.xml": _pptx_slide(
            title="Acme Slide Title",
            body_paras=["Body text for round-trip verification."]),
        # media entry — must be stripped
        "ppt/media/image1.png": FAKE_PNG,
        # thumbnail — must be stripped
        "docProps/thumbnail.jpeg": FAKE_JPEG,
    }
    with zipfile.ZipFile(deck_path, "w") as z:
        for member, data in parts.items():
            if isinstance(data, str):
                z.writestr(member, data)
            else:
                z.writestr(member, data)
    return deck_path


def _zip_members(data: bytes):
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
        return z.namelist()


def _zip_entry(data: bytes, name: str) -> bytes:
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
        return z.read(name)


def test_strip_media_pptx_drops_media_and_thumbnail(tmp_path):
    """Stored raw-KU body must lack ppt/media/* and docProps/thumbnail.* but
    be smaller than the original, and retain all XML parts byte-identical."""
    docs = tmp_path / "docs"
    deck = make_pptx_with_media(docs)
    original = deck.read_bytes()

    lib = Librarian(Store(tmp_path / "mem"))
    office.ingest_office(lib, docs, "dev", "ingest media deck")

    ku_id = "docs:slides/Acme Media Deck.pptx"
    stored = lib.read_body(ku_id)

    # Must be smaller (media dropped)
    assert len(stored) < len(original)

    # Dropped entries absent
    members = _zip_members(stored)
    assert "ppt/media/image1.png" not in members
    assert "docProps/thumbnail.jpeg" not in members

    # XML parts byte-identical to original
    for xml_part in ("[Content_Types].xml", "ppt/presentation.xml",
                     "ppt/slides/slide1.xml"):
        assert _zip_entry(stored, xml_part) == _zip_entry(original, xml_part), \
            f"{xml_part} content changed after strip"


def test_strip_media_round_trip_engine_parses_stripped(tmp_path):
    """Write the stored (stripped) body to a temp file and verify the engine's
    pptx extractor can still parse slide titles and body text from it."""
    docs = tmp_path / "docs"
    make_pptx_with_media(docs)

    lib = Librarian(Store(tmp_path / "mem"))
    office.ingest_office(lib, docs, "dev", "ingest media deck")

    stored = lib.read_body("docs:slides/Acme Media Deck.pptx")

    # Write stripped bytes to a temp file and re-parse via the office digest
    out_dir = tmp_path / "reparse"
    out_dir.mkdir()
    deck_copy = out_dir / "Acme Media Deck.pptx"
    deck_copy.write_bytes(stored)

    d = office.parse_office(out_dir, strip_media=False)   # already stripped
    assert len(d.documents) == 1
    rec = d.documents[0]
    assert rec.doc_type == "pptx"
    assert "Acme Slide Title" in rec.text
    assert "Body text for round-trip verification." in rec.text


def test_strip_media_determinism(tmp_path):
    """Stripping the same input twice must produce identical bytes (I9)."""
    docs = tmp_path / "docs"
    deck = make_pptx_with_media(docs)
    original = deck.read_bytes()

    from librarian.digest.office import _strip_media
    first = _strip_media(original)
    second = _strip_media(original)
    assert first == second


def test_strip_media_reingest_noop(tmp_path):
    """Re-ingesting the same deck (with media) must be an I9 no-op: the
    second Report shows it unchanged and the manifest generation does not
    advance."""
    docs = tmp_path / "docs"
    make_pptx_with_media(docs)

    lib = Librarian(Store(tmp_path / "mem"))
    office.ingest_office(lib, docs, "dev", "first ingest")
    gen = lib.manifest.generation

    rep, _ = office.ingest_office(lib, docs, "dev", "second ingest — must no-op")
    assert rep.unchanged
    assert lib.manifest.generation == gen


def test_strip_media_free_docx_identity(tmp_path):
    """A media-free docx must produce the ORIGINAL bytes object unchanged
    (identity — not merely equal)."""
    docs = make_docs_dir(tmp_path)

    from librarian.digest.office import _strip_media
    original = (docs / DOCX_REL).read_bytes()
    result = _strip_media(original)
    assert result is original, "media-free docx must return original object unchanged"


def test_strip_media_pdf_passthrough(tmp_path):
    """Non-OOXML (non-PK magic) bytes must pass through verbatim.  We test
    this directly on _strip_media without needing pypdf installed."""
    from librarian.digest.office import _strip_media

    pdf_like = b"%PDF-1.4 fake pdf content" + b"\x00" * 128
    assert _strip_media(pdf_like) is pdf_like


def test_strip_media_false_keeps_original(tmp_path):
    """strip_media=False must store the original file bytes verbatim."""
    docs = tmp_path / "docs"
    deck = make_pptx_with_media(docs)
    original = deck.read_bytes()

    lib = Librarian(Store(tmp_path / "mem"))
    office.ingest_office(lib, docs, "dev", "ingest no-strip", strip_media=False)

    stored = lib.read_body("docs:slides/Acme Media Deck.pptx")
    assert stored == original


# --------------------------------------------------------------------------- #
# graph accumulation across ingests (the data-loss fix — see _graphmerge)
# --------------------------------------------------------------------------- #
def _docfiles(g):
    return {n["source_path"] for n in g["nodes"] if n["type"] == "docfile"}


def test_second_digest_accumulates_the_graph(tmp_path):
    """The reported bug: a 2nd ingest must NOT drop earlier digestions from the
    structure graph. After ingesting a folder then a separate single doc, all
    three documents remain in the graph (and their raw KUs are untouched)."""
    lib = Librarian(Store(tmp_path / "mem"))
    office.ingest_office(lib, make_docs_dir(tmp_path), "dev",
                         "ingest sample office documents")
    assert _docfiles(office.load_graph(lib)) == {DOCX_REL, XLSX_REL}

    later = _docx(tmp_path / "later" / "Roadmap.docx",
                  _para("Roadmap", style="Heading1"), _para("Q3 plans."))
    office.ingest_office(lib, later, "dev", "ingest a later single office doc")

    assert _docfiles(office.load_graph(lib)) == {DOCX_REL, XLSX_REL, "Roadmap.docx"}
    assert lib.get(f"docs:{DOCX_REL}") is not None        # earlier raw KU survives too


def test_reingest_unchanged_office_is_a_noop(tmp_path):
    """Byte-identical merged graph -> I9 reports the graph KU unchanged, no
    generation bump (the merge must not churn on a no-op re-ingest)."""
    lib = Librarian(Store(tmp_path / "mem"))
    docs = make_docs_dir(tmp_path)
    office.ingest_office(lib, docs, "dev", "ingest sample office documents")
    gen = lib.manifest.generation
    rep, _ = office.ingest_office(lib, docs, "dev",
                                  "re-ingest identical office documents")
    assert lib.manifest.generation == gen
    assert office.GRAPH_ID in rep.unchanged


def test_reingesting_an_edited_doc_replaces_its_subgraph(tmp_path):
    """Editing a document changes its content hash (and thus its node ids); the
    OLD content-hash subgraph must be pruned, leaving exactly one current
    subgraph for that path — no stale sections accumulating."""
    lib = Librarian(Store(tmp_path / "mem"))
    _docx(tmp_path / "Spec.docx", _para("Overview", style="Heading1"),
          _para("First."), _para("Legacy section", style="Heading2"),
          _para("old body"))
    office.ingest_office(lib, tmp_path / "Spec.docx", "dev", "ingest a spec doc")
    old_fids = {n["id"].split("/", 1)[1] for n in office.load_graph(lib)["nodes"]
                if n["type"] == "docfile"}

    _docx(tmp_path / "Spec.docx", _para("Overview", style="Heading1"),
          _para("Rewritten, shorter."))                  # different bytes -> new fid
    office.ingest_office(lib, tmp_path / "Spec.docx", "dev",
                         "re-ingest the edited spec doc")

    g = office.load_graph(lib)
    docfiles = [n for n in g["nodes"] if n["type"] == "docfile"]
    assert len(docfiles) == 1 and docfiles[0]["source_path"] == "Spec.docx"
    new_fid = docfiles[0]["id"].split("/", 1)[1]
    assert new_fid not in old_fids
    # no node (and no edge endpoint) still carries the stale content hash
    assert all(old not in n["id"] for n in g["nodes"] for old in old_fids)
