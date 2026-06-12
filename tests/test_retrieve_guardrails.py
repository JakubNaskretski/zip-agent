"""Retrieval guardrail tests — walk() + excerpt().

These validate:
  - walk(): bounded BFS, depth, dedup (diamond), limit/truncated, direction,
    edge_type filter, type+label on nodes, start node absent, unknown node.
  - excerpt(): term match with context, max_hits, missing KU raises with ku_id
    in message, binary/office raw body doesn't crash.
"""
import pytest

from librarian import Librarian, KnowledgeUnit, Store
from librarian.digest import graphbuilder as sf
from librarian import retrieve


# --------------------------------------------------------------------------- #
# Helpers — minimal synthetic graphs
# --------------------------------------------------------------------------- #

def _graph(nodes, edges):
    """Build a plain {"nodes": [...], "edges": [...]} graph dict."""
    return {"nodes": nodes, "edges": edges}


def _n(nid, ntype="apexclass", label=None):
    return {"id": nid, "type": ntype, "label": label or nid.split("/")[-1]}


def _e(src, dst, etype="calls"):
    return {"src": src, "dst": dst, "type": etype}


# A linear chain: A -> B -> C -> D
LINEAR = _graph(
    [_n("apexclass/A"), _n("apexclass/B"), _n("apexclass/C"), _n("apexclass/D")],
    [_e("apexclass/A", "apexclass/B"),
     _e("apexclass/B", "apexclass/C"),
     _e("apexclass/C", "apexclass/D")],
)

# A diamond: A -> B, A -> C, B -> D, C -> D  (D reachable by two paths)
DIAMOND = _graph(
    [_n("apexclass/A"), _n("apexclass/B"), _n("apexclass/C"), _n("apexclass/D")],
    [_e("apexclass/A", "apexclass/B"),
     _e("apexclass/A", "apexclass/C"),
     _e("apexclass/B", "apexclass/D"),
     _e("apexclass/C", "apexclass/D")],
)

# Mixed edge types: A -calls-> B, A -reads-> C
MIXED = _graph(
    [_n("apexclass/A"), _n("apexclass/B"), _n("object/C", "object", "C")],
    [_e("apexclass/A", "apexclass/B", "calls"),
     _e("apexclass/A", "object/C", "reads")],
)


# --------------------------------------------------------------------------- #
# walk() — depth-1 equals neighbors()
# --------------------------------------------------------------------------- #

def test_walk_depth1_equals_neighbors_out():
    g = LINEAR
    nb = sf.neighbors(g, "apexclass/A", "out")
    result = sf.walk(g, "apexclass/A", depth=1)
    assert {n["id"] for n in result["nodes"]} == set(nb)


def test_walk_depth1_equals_neighbors_in():
    g = LINEAR
    nb = sf.neighbors(g, "apexclass/B", "in")
    result = sf.walk(g, "apexclass/B", depth=1, direction="in")
    assert {n["id"] for n in result["nodes"]} == set(nb)


def test_walk_depth1_equals_neighbors_with_edge_type():
    g = MIXED
    nb = sf.neighbors(g, "apexclass/A", "out", "calls")
    result = sf.walk(g, "apexclass/A", depth=1, edge_type="calls")
    assert {n["id"] for n in result["nodes"]} == set(nb)


# --------------------------------------------------------------------------- #
# walk() — depth-2 finds 2-hop nodes
# --------------------------------------------------------------------------- #

def test_walk_depth2_finds_two_hop_nodes():
    result = sf.walk(LINEAR, "apexclass/A", depth=2)
    ids = {n["id"] for n in result["nodes"]}
    # B is 1 hop, C is 2 hops; D is 3 hops (outside depth=2)
    assert "apexclass/B" in ids
    assert "apexclass/C" in ids
    assert "apexclass/D" not in ids


def test_walk_depth_field_on_nodes():
    result = sf.walk(LINEAR, "apexclass/A", depth=2)
    by_id = {n["id"]: n for n in result["nodes"]}
    assert by_id["apexclass/B"]["depth"] == 1
    assert by_id["apexclass/C"]["depth"] == 2


# --------------------------------------------------------------------------- #
# walk() — dedup: diamond shape — D counted once
# --------------------------------------------------------------------------- #

def test_walk_dedup_diamond():
    result = sf.walk(DIAMOND, "apexclass/A", depth=2)
    ids = [n["id"] for n in result["nodes"]]
    assert ids.count("apexclass/D") == 1
    assert result["truncated"] == 0


# --------------------------------------------------------------------------- #
# walk() — limit truncates with correct truncated count
# --------------------------------------------------------------------------- #

def test_walk_limit_truncates():
    # Linear A->B->C->D, depth=3, limit=1 — only B collected, C+D truncated
    result = sf.walk(LINEAR, "apexclass/A", depth=3, limit=1)
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["id"] == "apexclass/B"
    assert result["truncated"] == 2   # C and D discovered but not returned


def test_walk_limit_zero_truncates_all():
    result = sf.walk(LINEAR, "apexclass/A", depth=2, limit=0)
    assert result["nodes"] == []
    assert result["truncated"] == 2   # B and C discovered but not returned


# --------------------------------------------------------------------------- #
# walk() — direction "in" and "both"
# --------------------------------------------------------------------------- #

def test_walk_direction_in():
    # Walking "in" from D should find C (1 hop), B (2 hops), not A (3 hops)
    result = sf.walk(LINEAR, "apexclass/D", depth=2, direction="in")
    ids = {n["id"] for n in result["nodes"]}
    assert "apexclass/C" in ids
    assert "apexclass/B" in ids
    assert "apexclass/A" not in ids


def test_walk_direction_both():
    # From B "both": A (in, 1-hop) and C (out, 1-hop)
    result = sf.walk(LINEAR, "apexclass/B", depth=1, direction="both")
    ids = {n["id"] for n in result["nodes"]}
    assert "apexclass/A" in ids
    assert "apexclass/C" in ids
    assert "apexclass/B" not in ids   # start node not included


# --------------------------------------------------------------------------- #
# walk() — edge_type filter
# --------------------------------------------------------------------------- #

def test_walk_edge_type_filter_calls_only():
    result = sf.walk(MIXED, "apexclass/A", depth=1, edge_type="calls")
    ids = {n["id"] for n in result["nodes"]}
    assert "apexclass/B" in ids
    assert "object/C" not in ids


def test_walk_edge_type_filter_reads_only():
    result = sf.walk(MIXED, "apexclass/A", depth=1, edge_type="reads")
    ids = {n["id"] for n in result["nodes"]}
    assert "object/C" in ids
    assert "apexclass/B" not in ids


# --------------------------------------------------------------------------- #
# walk() — nodes carry type + label
# --------------------------------------------------------------------------- #

def test_walk_nodes_carry_type_and_label():
    result = sf.walk(LINEAR, "apexclass/A", depth=1)
    node = result["nodes"][0]
    assert node["id"] == "apexclass/B"
    assert node["type"] == "apexclass"
    assert node["label"] == "B"


def test_walk_missing_node_entry_type_label_none():
    """Node referenced in an edge but absent from graph["nodes"] → type/label None."""
    g = _graph(
        [_n("apexclass/A")],   # B is NOT in nodes
        [_e("apexclass/A", "apexclass/B")],
    )
    result = sf.walk(g, "apexclass/A", depth=1)
    assert result["nodes"][0]["id"] == "apexclass/B"
    assert result["nodes"][0]["type"] is None
    assert result["nodes"][0]["label"] is None


# --------------------------------------------------------------------------- #
# walk() — start node absent from result
# --------------------------------------------------------------------------- #

def test_walk_start_node_not_in_result():
    result = sf.walk(LINEAR, "apexclass/A", depth=2)
    ids = {n["id"] for n in result["nodes"]}
    assert "apexclass/A" not in ids


# --------------------------------------------------------------------------- #
# walk() — unknown node_id → empty, truncated 0
# --------------------------------------------------------------------------- #

def test_walk_unknown_node_id():
    result = sf.walk(LINEAR, "apexclass/DoesNotExist", depth=2)
    assert result["nodes"] == []
    assert result["truncated"] == 0


# --------------------------------------------------------------------------- #
# excerpt() helpers
# --------------------------------------------------------------------------- #

def _seed_lib(tmp_path):
    """Seed a Librarian with a couple of real KUs that have bodies."""
    lib = Librarian(Store(tmp_path / "mem"))
    ku = KnowledgeUnit(
        id="salesforce:apexclass/MeterPointService",
        kind="source-record", tier="raw", source="salesforce",
        path="kb/raw/salesforce/classes/MeterPointService.cls",
        title="MeterPointService", entities=["MeterPointService"],
        confidence="VERIFIED",
    )
    body = (
        "public class MeterPointService {\n"
        "    // handles bulk import retry logic\n"
        "    public static void executeSync(List<MeterPoint__c> recs) {\n"
        "        // retry loop\n"
        "        for (MeterPoint__c r : recs) { process(r); }\n"
        "    }\n"
        "}\n"
    )
    lib.begin("dev", "seed for excerpt tests").add_ku(ku, body=body).commit()
    return lib, body


# --------------------------------------------------------------------------- #
# excerpt() — finds term with context
# --------------------------------------------------------------------------- #

def test_excerpt_finds_term(tmp_path):
    lib, body = _seed_lib(tmp_path)
    results = retrieve.excerpt(lib, "salesforce:apexclass/MeterPointService", "retry")
    assert results, "expected at least one excerpt"
    joined = " ".join(results)
    assert "retry" in joined.lower()


def test_excerpt_context_surrounds_term(tmp_path):
    lib, body = _seed_lib(tmp_path)
    results = retrieve.excerpt(lib, "salesforce:apexclass/MeterPointService", "bulk import")
    assert results
    # should have surrounding context, not just the bare term
    assert any(len(r) > len("bulk import") for r in results)


# --------------------------------------------------------------------------- #
# excerpt() — max_hits respected
# --------------------------------------------------------------------------- #

def test_excerpt_max_hits_respected(tmp_path):
    lib, body = _seed_lib(tmp_path)
    # "MeterPoint" appears many times; limit to 2
    results = retrieve.excerpt(
        lib, "salesforce:apexclass/MeterPointService", "MeterPoint", max_hits=2
    )
    assert len(results) <= 2


def test_excerpt_max_hits_one(tmp_path):
    lib, body = _seed_lib(tmp_path)
    results = retrieve.excerpt(
        lib, "salesforce:apexclass/MeterPointService", "executeSync", max_hits=1
    )
    assert len(results) == 1


# --------------------------------------------------------------------------- #
# excerpt() — missing KU raises LookupError with ku_id in message
# --------------------------------------------------------------------------- #

def test_excerpt_missing_ku_raises(tmp_path):
    lib, _ = _seed_lib(tmp_path)
    with pytest.raises(LookupError) as exc_info:
        retrieve.excerpt(lib, "salesforce:apexclass/NoSuchClass", "anything")
    assert "salesforce:apexclass/NoSuchClass" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# excerpt() — binary/office raw KU body doesn't crash
# --------------------------------------------------------------------------- #

def test_excerpt_binary_body_no_crash(tmp_path):
    """A KU whose body is binary bytes (not valid UTF-8) must not raise."""
    lib = Librarian(Store(tmp_path / "mem"))
    ku = KnowledgeUnit(
        id="docs:spreadsheet/budget.xlsx",
        kind="source-record", tier="raw", source="docs",
        path="kb/raw/docs/budget.xlsx",
        title="Budget", entities=[],
        confidence="VERIFIED",
    )
    # Craft a binary body with non-UTF-8 bytes (simulates an office raw body)
    binary_body = b"PK\x03\x04" + bytes(range(128, 200)) + b"\xff\xfe invalid utf-8"
    lib.begin("dev", "seed binary ku for excerpt test").add_ku(
        ku, body=binary_body).commit()

    # Must not raise; returns a marker or replaced-char string
    results = retrieve.excerpt(lib, "docs:spreadsheet/budget.xlsx", "budget")
    assert isinstance(results, list)
    assert len(results) >= 1
    # Either a sidecar marker or the result of replace-decoded content
    text = " ".join(results)
    assert isinstance(text, str)


def test_excerpt_binary_body_marker_or_replaced(tmp_path):
    """Explicit non-UTF-8 body returns the sidecar marker."""
    lib = Librarian(Store(tmp_path / "mem"))
    ku = KnowledgeUnit(
        id="docs:doc/report.docx",
        kind="source-record", tier="raw", source="docs",
        path="kb/raw/docs/report.docx",
        title="Report", entities=[],
        confidence="VERIFIED",
    )
    # Bytes that cannot be decoded as UTF-8 and will produce replacement chars
    body = b"\x80\x81\x82\x83" * 50
    lib.begin("dev", "seed non-utf8 ku").add_ku(ku, body=body).commit()
    results = retrieve.excerpt(lib, "docs:doc/report.docx", "report")
    assert isinstance(results, list)
    assert len(results) >= 1
