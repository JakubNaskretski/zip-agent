"""Maintenance — remove / rename / reconcile a base source, and the work links
that pointed at it. Uses markdown docs (simple, deterministic) as the base source.
"""
import pytest

from runtime import maintain, navigate, search, work
from runtime.ingest import digest_to_tree
from runtime.storage import Workspace


def _docs_ws(tmp_path, files):
    src = tmp_path / "docs"
    src.mkdir()
    for name, body in files.items():
        (src / name).write_text(body, "utf-8")
    ws = Workspace(None, str(tmp_path / "work"))
    digest_to_tree(ws, "docs", str(src))
    return ws


def _docfile(ws, source_path):
    g = navigate.load_shard(ws, "docs")
    return next(n for n in g["nodes"]
                if n.get("type") == "docfile" and n.get("source_path") == source_path)


# --------------------------------------------------------------------------- #
# forget one file — nodes + raw + sidecar + the work edges to it
# --------------------------------------------------------------------------- #
def test_forget_removes_file_nodes_raw_and_work_edges(tmp_path):
    ws = _docs_ws(tmp_path, {"a.md": "# A\n\nalpha body\n", "b.md": "# B\n\nbeta body\n"})
    a = _docfile(ws, "a.md")
    work.link(ws, work.add_node(ws, "topic"), f"docs:{a['id']}", kind="about")
    assert ws.exists("kb/raw/docs/a.md")

    res = maintain.forget(ws, "docs", "a.md")
    assert res["nodes_dropped"] >= 1 and res["work_edges_cleaned"] == 1

    g = navigate.load_shard(ws, "docs")
    assert not any(n.get("source_path") == "a.md" for n in g["nodes"])     # a's nodes gone
    assert any(n.get("source_path") == "b.md" for n in g["nodes"])         # b kept
    assert not ws.exists("kb/raw/docs/a.md") and not ws.exists("kb/raw/docs/a.md.txt")
    assert work.links_of(ws, f"docs:{a['id']}") == []                      # junction cleaned


# --------------------------------------------------------------------------- #
# reconcile — a file deleted straight out of the zip drops its node + work edge
# --------------------------------------------------------------------------- #
def test_reconcile_drops_nodes_for_deleted_files(tmp_path):
    ws = _docs_ws(tmp_path, {"a.md": "# A\n\nalpha body\n", "b.md": "# B\n\nbeta body\n"})
    a = _docfile(ws, "a.md")
    work.link(ws, work.add_node(ws, "t"), f"docs:{a['id']}", kind="about")
    # someone deletes the raw file directly (editing the zip), graph not yet updated
    ws.remove("kb/raw/docs/a.md")
    ws.remove("kb/raw/docs/a.md.txt")

    res = maintain.reconcile(ws)
    assert res["sources"]["docs"]["nodes_dropped"] >= 1 and res["work_edges_cleaned"] == 1
    g = navigate.load_shard(ws, "docs")
    assert not any(n.get("source_path") == "a.md" for n in g["nodes"])
    assert any(n.get("source_path") == "b.md" for n in g["nodes"])         # untouched file kept
    # a re-ingest that simply omitted a.md would NOT have dropped it (absence != deletion);
    # reconcile is the deliberate sweep.


# --------------------------------------------------------------------------- #
# remove a whole source
# --------------------------------------------------------------------------- #
def test_remove_source_nukes_files_shard_and_links(tmp_path):
    ws = _docs_ws(tmp_path, {"a.md": "# A\n\nalpha body\n"})
    did = _docfile(ws, "a.md")["id"]
    work.link(ws, work.add_node(ws, "t"), f"docs:{did}", kind="about")

    res = maintain.remove_source(ws, "docs")
    assert res["files_removed"] >= 1 and res["work_edges_cleaned"] == 1
    assert not ws.exists("graph/docs.json")
    assert "docs" not in navigate.present_sources(ws)
    assert search.search(ws, "alpha", source="docs") == []


# --------------------------------------------------------------------------- #
# rename a file — node id stable, source_path repointed, work link survives
# --------------------------------------------------------------------------- #
def test_rename_moves_file_and_repoints_source_path(tmp_path):
    ws = _docs_ws(tmp_path, {"old.md": "# Spec\n\nthe spec body\n"})
    df = _docfile(ws, "old.md")
    old_id = df["id"]
    work.link(ws, work.add_node(ws, "t"), f"docs:{old_id}", kind="about")

    res = maintain.rename(ws, "docs", "old.md", "new.md")
    assert res["nodes_repointed"] >= 1
    assert not ws.exists("kb/raw/docs/old.md") and ws.exists("kb/raw/docs/new.md")

    df2 = next(n for n in navigate.load_shard(ws, "docs")["nodes"] if n["id"] == old_id)
    assert df2["source_path"] == "new.md"                                  # repointed
    assert "spec body" in (navigate.read_source(ws, "docs", df2) or "")    # reads new path
    assert work.links_of(ws, f"docs:{old_id}")                             # work link still resolves


# --------------------------------------------------------------------------- #
# deep review/prune catch a base-ref dangle (off by default)
# --------------------------------------------------------------------------- #
def test_deep_review_and_prune_base_dangle(tmp_path):
    ws = _docs_ws(tmp_path, {"a.md": "# A\n\nalpha body\n"})
    did = _docfile(ws, "a.md")["id"]
    cid = work.add_node(ws, "t")
    work.link(ws, cid, f"docs:{did}", kind="about")              # valid
    work.link(ws, cid, "docs:docfile/doesnotexist", kind="about")  # dangling base ref

    assert "base_dangles" not in work.review(ws)                 # off by default (cheap)
    assert work.review(ws, deep=True)["base_dangles"]            # flagged when asked
    assert work.prune_orphans(ws, deep=True) >= 1
    assert work.review(ws, deep=True)["base_dangles"] == []
    assert any(l["other"] == f"docs:{did}" for l in work.links_of(ws, cid))  # valid edge survived


# --------------------------------------------------------------------------- #
# rename onto an existing destination is refused — it would destroy that file
# --------------------------------------------------------------------------- #
def test_rename_to_existing_destination_is_refused(tmp_path):
    ws = _docs_ws(tmp_path, {"keep.md": "# Keep\n\nkeep body\n",
                             "other.md": "# Other\n\nother body\n"})
    with pytest.raises(FileExistsError):
        maintain.rename(ws, "docs", "keep.md", "other.md")
    # nothing moved or clobbered: both files + both nodes intact
    assert ws.exists("kb/raw/docs/keep.md") and ws.exists("kb/raw/docs/other.md")
    assert "keep body" in (navigate.read_source(ws, "docs", _docfile(ws, "keep.md")) or "")
    assert "other body" in (navigate.read_source(ws, "docs", _docfile(ws, "other.md")) or "")


# --------------------------------------------------------------------------- #
# deep prune leaves a link into a NOT-ingested source alone (absent != deleted)
# --------------------------------------------------------------------------- #
def test_deep_prune_preserves_links_into_absent_source(tmp_path):
    ws = _docs_ws(tmp_path, {"a.md": "# A\n\nalpha body\n"})
    cid = work.add_node(ws, "concept")
    work.link(ws, cid, "salesforce:object/Account", kind="relates-to")   # salesforce never ingested
    assert not ws.exists("graph/salesforce.json")

    assert work.review(ws, deep=True)["base_dangles"] == []   # absent shard not treated as a dangle
    assert work.prune_orphans(ws, deep=True) == 0             # so nothing dropped
    assert any(l["other"] == "salesforce:object/Account" for l in work.links_of(ws, cid))


# --------------------------------------------------------------------------- #
# reconcile booted from a zip base drops nothing — every base file is in the archive
# --------------------------------------------------------------------------- #
def test_reconcile_from_zip_base_drops_nothing(tmp_path):
    build = _docs_ws(tmp_path, {"a.md": "# A\n\nalpha body\n", "b.md": "# B\n\nbeta body\n"})
    a = _docfile(build, "a.md")
    work.link(build, work.add_node(build, "t"), f"docs:{a['id']}", kind="about")
    zip_path = build.export(str(tmp_path / "memory.zip"))

    ws = Workspace(str(zip_path), str(tmp_path / "session_work"))   # zip base, empty overlay
    res = maintain.reconcile(ws)
    assert res["sources"] == {} and res["work_edges_cleaned"] == 0  # base files all present
    g = navigate.load_shard(ws, "docs")
    assert any(n.get("source_path") == "a.md" for n in g["nodes"])
    assert any(n.get("source_path") == "b.md" for n in g["nodes"])
    assert work.links_of(ws, f"docs:{a['id']}")                    # work link untouched
