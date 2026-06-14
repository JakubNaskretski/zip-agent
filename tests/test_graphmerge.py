"""Unit tests for the digest graph-merge helper — the shared logic that makes
every ``*:graph/*`` aggregate KU ACCUMULATE across ingests instead of being
replaced (the data-loss bug where a 2nd digest dropped earlier digestions from
the graph).

These pin the three correctness rules an adversarial review surfaced, at the
level all five adapters share:
  * source_path-scoped supersession (deleted child nodes / dropped edges go);
  * never downgrade a real node to an ``external`` stub (cross-batch refs);
  * diagnostics merge so an unchanged re-ingest is BYTE-IDENTICAL (I9 no-op).
Comparison uses the real persistence serializer, i.e. exactly the bytes the
Librarian content-hashes for its idempotency check.
"""
from librarian.digest import _graphmerge, office  # office import wires vendor/ onto sys.path
from graphbuilder import persistence  # noqa: E402


def n(nid, source_path, **extra):
    return {"id": nid, "source_path": source_path, **extra}


def e(src, type_, dst):
    return {"src": src, "type": type_, "dst": dst}


def G(nodes=(), edges=(), unresolved=(), errors=()):
    return {"nodes": list(nodes), "edges": list(edges),
            "unresolved": list(unresolved), "errors": list(errors)}


def canon(graph):
    """The exact JSON bytes the Librarian would hash for I9."""
    return persistence.to_json(graph)


# --------------------------------------------------------------------------- #
# accumulation — the headline fix
# --------------------------------------------------------------------------- #
def test_disjoint_batches_accumulate():
    existing = G([n("docfile/a", "a.docx")])
    new = G([n("docfile/b", "b.docx")])
    merged = _graphmerge.merge_graphs(existing, new)
    assert {x["id"] for x in merged["nodes"]} == {"docfile/a", "docfile/b"}


def test_first_ingest_into_empty_is_the_new_graph():
    new = G([n("p", "f"), n("c", "f")], [e("p", "contains", "c")])
    assert canon(_graphmerge.merge_graphs(_graphmerge.empty_graph(), new)) == canon(new)


# --------------------------------------------------------------------------- #
# idempotency — an unchanged re-ingest must be byte-identical (I9 no-op)
# --------------------------------------------------------------------------- #
def test_reingest_unchanged_whole_corpus_is_byte_identical():
    g = G([n("p", "f"), n("c", "f")], [e("p", "contains", "c")],
          unresolved=[{"src": "p", "type": "x", "dst": "?", "reason": "r"}],
          errors=[{"source": "s", "path": "f", "error": "boom"}])
    once = _graphmerge.merge_graphs(_graphmerge.empty_graph(), g)
    twice = _graphmerge.merge_graphs(once, g)
    assert canon(twice) == canon(once)


def test_reingest_one_unchanged_file_of_many_is_byte_identical():
    stored = G([n("a1", "a"), n("b1", "b")],
               [e("a1", "rel", "b1")],
               errors=[{"source": "s", "path": "a", "error": "ea"},
                       {"source": "s", "path": "b", "error": "eb"}])
    # re-parse of file "a" alone (b not in this batch); a unchanged
    batch_a = G([n("a1", "a")], [e("a1", "rel", "b1")],
                errors=[{"source": "s", "path": "a", "error": "ea"}])
    merged = _graphmerge.merge_graphs(stored, batch_a)
    assert canon(merged) == canon(stored)  # b1, the a->b edge, and b's error all survive


# --------------------------------------------------------------------------- #
# external-stub provenance — cross-batch references must not corrupt real nodes
# --------------------------------------------------------------------------- #
def test_external_stub_never_downgrades_a_real_node():
    existing = G([n("issue/A", "A.json")])                       # A ingested for real
    # a later batch for B references A -> standalone parse mints an external stub for A
    new = G([n("issue/B", "B.json"),
             n("issue/A", "B.json", external=True, label="A")],
            [e("issue/B", "links-to", "issue/A")])
    merged = _graphmerge.merge_graphs(existing, new)
    a = next(x for x in merged["nodes"] if x["id"] == "issue/A")
    assert not a.get("external")                                 # stayed real
    assert {x["id"] for x in merged["nodes"]} == {"issue/A", "issue/B"}
    assert e("issue/B", "links-to", "issue/A") in merged["edges"]


def test_cross_batch_reference_does_not_oscillate():
    existing = G([n("issue/A", "A.json")])
    new = G([n("issue/B", "B.json"),
             n("issue/A", "B.json", external=True, label="A")],
            [e("issue/B", "links-to", "issue/A")])
    first = _graphmerge.merge_graphs(existing, new)
    again = _graphmerge.merge_graphs(first, new)                 # re-ingest B's batch
    assert canon(again) == canon(first)                         # fixpoint, no flip-flop


# --------------------------------------------------------------------------- #
# supersession — a re-ingested record's removed children/edges must disappear
# --------------------------------------------------------------------------- #
def test_deleted_child_node_is_pruned_on_reingest():
    stored = G([n("obj/Account", "Account.xml"),
                n("field/Account.Name", "Account.xml"),
                n("field/Account.Legacy", "Account.xml")],
               [e("obj/Account", "has", "field/Account.Name"),
                e("obj/Account", "has", "field/Account.Legacy")])
    # edited file: Legacy field removed
    new = G([n("obj/Account", "Account.xml"),
             n("field/Account.Name", "Account.xml")],
            [e("obj/Account", "has", "field/Account.Name")])
    merged = _graphmerge.merge_graphs(stored, new)
    ids = {x["id"] for x in merged["nodes"]}
    assert "field/Account.Legacy" not in ids
    assert e("obj/Account", "has", "field/Account.Legacy") not in merged["edges"]


def test_dropped_edge_to_shared_node_is_not_resurrected():
    # the adversary's core case: record stops asserting an edge to a SHARED node
    stored = G([n("flow/orders", "orders.xml"), n("conn/http", "shared.xml")],
               [e("flow/orders", "uses", "conn/http")])
    new = G([n("flow/orders", "orders.xml")], [])               # orders no longer uses http
    merged = _graphmerge.merge_graphs(stored, new)
    assert {x["id"] for x in merged["nodes"]} == {"flow/orders", "conn/http"}  # shared kept
    assert merged["edges"] == []                                # stale edge gone


def test_edge_from_other_record_to_reingested_node_survives():
    # G references file-A's node; re-ingesting A unchanged must keep G's inbound edge
    stored = G([n("a1", "a"), n("g1", "g")], [e("g1", "refs", "a1")])
    batch_a = G([n("a1", "a")], [])                             # A re-parsed, no own edges
    merged = _graphmerge.merge_graphs(stored, batch_a)
    assert e("g1", "refs", "a1") in merged["edges"]


# --------------------------------------------------------------------------- #
# diagnostics — scoped, so other files' errors persist but fixed ones clear
# --------------------------------------------------------------------------- #
def test_diagnostics_scoped_to_reingested_file():
    stored = G([n("a1", "a")],
               errors=[{"source": "s", "path": "a", "error": "was-bad"},
                       {"source": "s", "path": "b", "error": "b-bad"}])
    new = G([n("a1", "a")])                                     # a now parses cleanly
    merged = _graphmerge.merge_graphs(stored, new)
    paths = {x.get("path") for x in merged["errors"]}
    assert paths == {"b"}                                       # a's fixed error gone, b's kept


# --------------------------------------------------------------------------- #
# load_existing — never resurrect a retired/tombstoned graph KU
# --------------------------------------------------------------------------- #
class _Entry:
    def __init__(self, status):
        self.status = status


class _FakeLib:
    def __init__(self, status, body):
        self._status, self._body = status, body

    def get(self, _id):
        return _Entry(self._status)

    def read_body(self, _id):
        return self._body


def test_load_existing_skips_non_active_graph():
    body = canon(G([n("x", "f")]))
    assert _graphmerge.load_existing(
        _FakeLib("retired", body), "g", persistence)["nodes"] == []
    assert _graphmerge.load_existing(
        _FakeLib("active", body), "g", persistence)["nodes"][0]["id"] == "x"
