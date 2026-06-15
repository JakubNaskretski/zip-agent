"""The agent work layer + markdown ingestion.

The work layer: the agent's own notes + the edges it draws (including cross-source
joins), in its own `graph/work.json` shard and `kb/work/` files — usable and
connected to the base sources via junction edges, navigable from either side,
and cleanable. Plus `.md` ingested as a regular document in the docs digest.
"""
from runtime import boot, navigate, search, work
from runtime.ingest import digest_to_tree
from runtime.storage import Workspace

import tests.test_digest_graphbuilder as sf_t


def _ws(tmp_path):
    return Workspace(None, str(tmp_path / "work"))


# --------------------------------------------------------------------------- #
# work notes — a file + a node + derived-from edges
# --------------------------------------------------------------------------- #
def test_write_note_creates_file_node_and_edges(tmp_path):
    ws = _ws(tmp_path)
    p = work.write_note(ws, "rfp/run/req-001", "Bulk import is met by X.",
                        title="Req 1", author="agent",
                        derived_from=["docs:docfile/abc123", "kb/raw/docs/spec.md"])
    assert p == "kb/work/rfp/run/req-001.md" and ws.exists(p)
    g = navigate.load_shard(ws, "work")
    nid = "work:note/rfp/run/req-001"
    assert any(n["id"] == nid and n["type"] == "note" and n.get("author") == "agent"
               for n in g["nodes"])
    df = {(e["src"], e["dst"]) for e in g["edges"] if e["type"] == "derived-from"}
    assert (nid, "docs:docfile/abc123") in df and (nid, "kb/raw/docs/spec.md") in df
    note = work.read_note(ws, "rfp/run/req-001")
    assert note["frontmatter"]["author"] == "agent" and "Bulk import" in note["body"]


# --------------------------------------------------------------------------- #
# the junction — link any two nodes, navigable from BOTH sides; cross-source join
# --------------------------------------------------------------------------- #
def test_link_join_and_links_of_both_sides(tmp_path):
    ws = _ws(tmp_path)
    cid = work.add_node(ws, "order-sync", label="Order Sync process")
    assert cid == "work:concept/order-sync"
    work.link(ws, cid, "docs:docfile/map1", kind="appears-in")
    work.link(ws, cid, "docs:docfile/deck5", kind="appears-in")

    # from the concept → both sources
    assert {l["other"] for l in work.links_of(ws, cid)} == {"docs:docfile/map1", "docs:docfile/deck5"}
    # from a SOURCE endpoint → back to the concept (junction navigable both ways)
    back = work.links_of(ws, "docs:docfile/map1")
    assert back and back[0]["other"] == cid and back[0]["direction"] == "in"
    # idempotent + unlink
    work.link(ws, cid, "docs:docfile/map1", kind="appears-in")            # dup -> no growth
    assert len([l for l in work.links_of(ws, cid)]) == 2
    assert work.unlink(ws, cid, "docs:docfile/deck5") == 1
    assert {l["other"] for l in work.links_of(ws, cid)} == {"docs:docfile/map1"}


def test_show_resolves_a_base_ref_to_its_source(tmp_path):
    sf_t.make_force_app(tmp_path)
    ws = _ws(tmp_path)
    digest_to_tree(ws, "salesforce", str(tmp_path / "force-app"))
    work.add_node(ws, "billing", label="Billing area")
    work.link(ws, "work:concept/billing", "salesforce:object/MeterPoint__c", kind="covers")

    shown = work.show(ws, "salesforce:object/MeterPoint__c")
    assert shown["source"] == "salesforce" and shown["node"]["id"] == "object/MeterPoint__c"
    # from that real source node, the work connected to it is discoverable
    assert any(l["other"] == "work:concept/billing"
               for l in work.links_of(ws, "salesforce:object/MeterPoint__c"))


# --------------------------------------------------------------------------- #
# keeping it tidy — stale notes + orphan edges, then prune
# --------------------------------------------------------------------------- #
def test_review_and_prune(tmp_path):
    ws = _ws(tmp_path)
    ws.write_bytes("kb/raw/docs/src.md", b"original")
    work.write_note(ws, "n1", "note", derived_from=["kb/raw/docs/src.md"])
    assert work.review(ws)["stale"] == []
    ws.write_bytes("kb/raw/docs/src.md", b"changed underneath")
    assert work.review(ws)["stale"]                              # source moved on

    # a dangling edge: linked to a work node that was never created (typo / forgot)
    work.link(ws, "work:concept/ghost", "work:note/n1", kind="relates-to")
    assert work.review(ws)["orphan_edges"]
    assert work.prune_orphans(ws) >= 1
    assert work.review(ws)["orphan_edges"] == []
    # remove_node is tidy on its own — it clears the edges touching it
    work.link(ws, "work:note/n1", "docs:docfile/x", kind="about")
    work.remove_node(ws, "work:note/n1")
    assert navigate.load_shard(ws, "work")["edges"] == []


def test_remove_note_clears_file_node_and_edges(tmp_path):
    ws = _ws(tmp_path)
    work.write_note(ws, "x", "body", derived_from=["docs:docfile/z"])
    assert ws.exists("kb/work/x.md")
    work.remove_note(ws, "x")
    assert not ws.exists("kb/work/x.md")
    g = navigate.load_shard(ws, "work")
    assert not any(n["id"] == "work:note/x" for n in g["nodes"]) and g["edges"] == []


# --------------------------------------------------------------------------- #
# discoverable + searchable + carried across an upgrade
# --------------------------------------------------------------------------- #
def test_work_layer_searchable_and_present(tmp_path):
    ws = _ws(tmp_path)
    work.write_note(ws, "topic", "the answer mentions WidgetService here", title="T")
    assert "work" in navigate.present_sources(ws)
    hits = search.search(ws, "WidgetService", source="work")
    assert hits and hits[0]["source"] == "work"


def test_migrate_carries_the_work_layer(tmp_path):
    from scripts.build_memory import migrate_to_lean
    ws = _ws(tmp_path)
    work.write_note(ws, "keep", "kept work", title="K", derived_from=["docs:docfile/q"])
    work.link(ws, "work:note/keep", "salesforce:object/Account", kind="about")
    deployed = ws.export(str(tmp_path / "deployed.zip"))

    _, memzip, _, counts = migrate_to_lean(str(deployed), "project", out_dir=tmp_path / "out")
    assert counts["work"] >= 1 and counts["graphs"] >= 1

    s = boot(str(memzip), str(tmp_path / "sess"))
    assert "work" in s.sources()
    g = s.shard("work")
    assert any(n["id"] == "work:note/keep" for n in g["nodes"])
    assert any(e["dst"] == "salesforce:object/Account" for e in g["edges"])
    assert s.ws.exists("kb/work/keep.md")


# --------------------------------------------------------------------------- #
# markdown ingested as a regular document in the docs digest
# --------------------------------------------------------------------------- #
def test_markdown_ingests_as_a_doc(tmp_path):
    src = tmp_path / "docs"
    src.mkdir()
    (src / "spec.md").write_text(
        "# Title\n\nIntro para.\n\n## Section A\n\nAlpha body.\n\n"
        "## Section B\n\nBeta body mentions retries.\n\n```\n# not a heading\n```\n",
        "utf-8")
    ws = _ws(tmp_path)
    digest_to_tree(ws, "docs", str(src))

    g = navigate.load_shard(ws, "docs")
    docfiles = [n for n in g["nodes"] if n["type"] == "docfile"]
    assert docfiles and docfiles[0].get("doc_type") == "markdown"
    labels = {n["label"] for n in g["nodes"] if n["type"] == "docsection"}
    assert {"Section A", "Section B"} <= labels
    assert "not a heading" not in labels                         # fenced code skipped
    assert ws.exists("kb/raw/docs/spec.md") and ws.exists("kb/raw/docs/spec.md.txt")
    assert search.search(ws, "retries", source="docs")           # text is searchable
