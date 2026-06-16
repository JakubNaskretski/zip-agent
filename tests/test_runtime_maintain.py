"""Maintenance — remove / rename / reconcile a base source, and the work links
that pointed at it. Uses markdown docs (simple, deterministic) as the base source.
"""
import zipfile

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


def _ids_for(ws, source_path):
    """Every node id a given file produced — a doc file ingests as >1 node (docfile +
    one docsection per heading), so the exact-count assertions below catch a partial
    drop/repoint that a bare ``>= 1`` would mask."""
    return {n["id"] for n in navigate.load_shard(ws, "docs")["nodes"]
            if n.get("source_path") == source_path}


# --------------------------------------------------------------------------- #
# forget one file — nodes + raw + sidecar + the work edges to it
# --------------------------------------------------------------------------- #
def test_forget_removes_file_nodes_raw_and_work_edges(tmp_path):
    ws = _docs_ws(tmp_path, {"a.md": "# A\n\nalpha body\n", "b.md": "# B\n\nbeta body\n"})
    a = _docfile(ws, "a.md")
    a_ids = _ids_for(ws, "a.md")
    assert len(a_ids) >= 2                                                  # >1 node, so a partial drop is catchable
    work.link(ws, work.add_node(ws, "topic"), f"docs:{a['id']}", kind="about")
    assert ws.exists("kb/raw/docs/a.md")

    res = maintain.forget(ws, "docs", "a.md")
    assert res["nodes_dropped"] == len(a_ids) and res["work_edges_cleaned"] == 1  # EVERY a-node dropped

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
    a_ids = _ids_for(ws, "a.md")
    work.link(ws, work.add_node(ws, "t"), f"docs:{a['id']}", kind="about")
    # someone deletes the raw file directly (editing the zip), graph not yet updated
    ws.remove("kb/raw/docs/a.md")
    ws.remove("kb/raw/docs/a.md.txt")

    res = maintain.reconcile(ws)
    assert res["sources"]["docs"]["nodes_dropped"] == len(a_ids) and res["work_edges_cleaned"] == 1
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
    files_before = ws.listing("kb/raw/docs/")                     # raw + .txt sidecar
    assert len(files_before) >= 2
    work.link(ws, work.add_node(ws, "t"), f"docs:{did}", kind="about")

    res = maintain.remove_source(ws, "docs")
    assert res["files_removed"] == len(files_before) and res["work_edges_cleaned"] == 1
    assert not ws.exists("graph/docs.json")
    assert "docs" not in navigate.present_sources(ws)
    assert search.search(ws, "alpha", source="docs") == []


# --------------------------------------------------------------------------- #
# rename a file — node id stable, source_path repointed, work link survives
# --------------------------------------------------------------------------- #
def test_rename_moves_file_and_repoints_source_path(tmp_path):
    # multi-heading so the file is >1 node (docfile + 2 docsections) — a rename that
    # repointed only the docfile and left a section behind would dangle, and must fail.
    ws = _docs_ws(tmp_path, {"old.md": "# Spec\n\nthe spec body\n\n## Detail\n\nmore detail here\n"})
    old_id = _docfile(ws, "old.md")["id"]
    old_ids = _ids_for(ws, "old.md")
    assert len(old_ids) >= 2
    work.link(ws, work.add_node(ws, "t"), f"docs:{old_id}", kind="about")

    res = maintain.rename(ws, "docs", "old.md", "new.md")
    assert res["nodes_repointed"] == len(old_ids)                          # EVERY node repointed
    assert not ws.exists("kb/raw/docs/old.md") and ws.exists("kb/raw/docs/new.md")

    g = navigate.load_shard(ws, "docs")
    assert not any(n.get("source_path") == "old.md" for n in g["nodes"])   # no survivor on the old path
    for nid in old_ids:                                                    # ids stable, all read the new file
        n = next(x for x in g["nodes"] if x["id"] == nid)
        assert n["source_path"] == "new.md"
        assert navigate.read_source(ws, "docs", n) is not None
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
    assert work.prune_orphans(ws, deep=True) == 1               # exactly the one dangling edge
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


# --------------------------------------------------------------------------- #
# reconcile must NOT prune a source whose ENTIRE raw tree is absent (a structure-
# only slice / not-shipped) — that is absence, not deletion. Catastrophic if wrong.
# --------------------------------------------------------------------------- #
def _structure_only_zip(ws, path):
    """Export ws, then rebuild a zip KEEPING graph/index but DROPPING every kb/raw/*
    — the structure-only configuration where reconcile must keep its hands off."""
    full = path.parent / (path.name + ".full.zip")
    ws.export(str(full))
    with zipfile.ZipFile(full) as zin, zipfile.ZipFile(path, "w") as zo:
        for n in zin.namelist():
            if not n.startswith("kb/raw/"):
                zo.writestr(n, zin.read(n))
    return path


def test_reconcile_skips_structure_only_slice(tmp_path):
    build = _docs_ws(tmp_path, {"a.md": "# A\n\nalpha\n", "b.md": "# B\n\nbeta\n"})
    sliced = _structure_only_zip(build, tmp_path / "slice.zip")
    ws = Workspace(str(sliced), str(tmp_path / "sess"))
    before = len(navigate.load_shard(ws, "docs")["nodes"])
    assert before == 4                                              # 2 files × (docfile + section)

    res = maintain.reconcile(ws)
    assert res["sources"] == {}                                    # NOTHING pruned
    assert res["skipped"]["docs"] == 2                             # both files' absence flagged as a slice
    assert len(navigate.load_shard(ws, "docs")["nodes"]) == before  # graph fully intact


# --------------------------------------------------------------------------- #
# rename refuses to clobber an existing DEST SIDECAR (.txt), not just the raw file
# --------------------------------------------------------------------------- #
def test_rename_refuses_existing_dest_sidecar(tmp_path):
    ws = _docs_ws(tmp_path, {"old.md": "# Spec\n\nthe spec body\n"})
    ws.write_text("kb/raw/docs/new.md.txt", "PRECIOUS")            # a stray dest sidecar, no dest raw
    assert not ws.exists("kb/raw/docs/new.md")

    with pytest.raises(FileExistsError):
        maintain.rename(ws, "docs", "old.md", "new.md")
    # nothing moved, the dest sidecar is untouched, the source is intact
    assert ws.read_text("kb/raw/docs/new.md.txt") == "PRECIOUS"
    assert ws.exists("kb/raw/docs/old.md")
    assert _docfile(ws, "old.md")["source_path"] == "old.md"


# --------------------------------------------------------------------------- #
# rename a.md -> a.md is rejected before any write (no self-clobber / no loss)
# --------------------------------------------------------------------------- #
def test_rename_same_path_is_safe(tmp_path):
    ws = _docs_ws(tmp_path, {"a.md": "# A\n\nalpha body\n"})
    with pytest.raises(FileExistsError):
        maintain.rename(ws, "docs", "a.md", "a.md")
    assert ws.exists("kb/raw/docs/a.md")
    assert "alpha body" in (navigate.read_source(ws, "docs", _docfile(ws, "a.md")) or "")


# --------------------------------------------------------------------------- #
# a maintenance verb on a BASE (zip) file survives a re-boot WITHOUT an export —
# the removed/renamed file must not resurrect from the read-only base.
# --------------------------------------------------------------------------- #
def test_maintenance_survives_reboot_without_export(tmp_path):
    build = _docs_ws(tmp_path, {"a.md": "# A\n\nALPHATOK\n", "b.md": "# B\n\nbeta\n"})
    v1 = build.export(str(tmp_path / "v1.zip"))
    overlay = str(tmp_path / "sess")

    ws = Workspace(str(v1), overlay)
    maintain.forget(ws, "docs", "a.md")
    maintain.rename(ws, "docs", "b.md", "bb.md")

    ws2 = Workspace(str(v1), overlay)                              # kernel died → reconnect, no export
    assert not ws2.exists("kb/raw/docs/a.md")                     # forgotten file stays gone
    assert not ws2.exists("kb/raw/docs/b.md")                     # renamed-away path stays gone
    assert ws2.exists("kb/raw/docs/bb.md")                        # new path present
    assert search.search(ws2, "ALPHATOK", source="docs") == []   # no searchable zombie
    g = navigate.load_shard(ws2, "docs")
    assert not any(n.get("source_path") in ("a.md", "b.md") for n in g["nodes"])
    assert any(n.get("source_path") == "bb.md" for n in g["nodes"])


# --------------------------------------------------------------------------- #
# the same removals, baked into an exported zip, round-trip into a fresh session
# --------------------------------------------------------------------------- #
def test_export_after_maintenance_round_trip(tmp_path):
    build = _docs_ws(tmp_path, {"gone.md": "# Gone\n\nGONETOK\n", "old.md": "# Old\n\nthe body\n"})
    v1 = build.export(str(tmp_path / "v1.zip"))

    ws = Workspace(str(v1), str(tmp_path / "sess"))
    maintain.forget(ws, "docs", "gone.md")
    maintain.rename(ws, "docs", "old.md", "new.md")
    v2 = ws.export(str(tmp_path / "v2.zip"))

    with zipfile.ZipFile(v2) as z:
        names = set(z.namelist())
    assert "kb/raw/docs/gone.md" not in names and "kb/raw/docs/gone.md.txt" not in names
    assert "kb/raw/docs/old.md" not in names
    assert "kb/raw/docs/new.md" in names and "kb/raw/docs/new.md.txt" in names

    ws2 = Workspace(str(v2), str(tmp_path / "fresh"))             # fresh session over the exported zip
    assert not ws2.exists("kb/raw/docs/gone.md")
    assert search.search(ws2, "GONETOK", source="docs") == []
    g = navigate.load_shard(ws2, "docs")
    assert not any(n.get("source_path") in ("gone.md", "old.md") for n in g["nodes"])
    nn = next(n for n in g["nodes"] if n.get("source_path") == "new.md" and n.get("type") == "docfile")
    assert navigate.read_source(ws2, "docs", nn) is not None


# --------------------------------------------------------------------------- #
# reconcile cleans the .txt sidecar of a deleted file (no searchable zombie)
# --------------------------------------------------------------------------- #
def test_reconcile_removes_orphaned_sidecar(tmp_path):
    ws = _docs_ws(tmp_path, {"a.md": "# A\n\nZOMBIETOK body\n", "b.md": "# B\n\nbeta\n"})
    ws.remove("kb/raw/docs/a.md")                                 # raw deleted, SIDECAR left behind
    assert ws.exists("kb/raw/docs/a.md.txt")
    assert search.search(ws, "ZOMBIETOK", source="docs")         # still searchable pre-sweep

    maintain.reconcile(ws)
    assert not ws.exists("kb/raw/docs/a.md.txt")                  # sidecar swept
    assert search.search(ws, "ZOMBIETOK", source="docs") == []   # zombie gone


# --------------------------------------------------------------------------- #
# reconcile is atomic — a corrupt sibling shard aborts BEFORE any source is pruned
# --------------------------------------------------------------------------- #
def test_reconcile_aborts_atomically_on_corrupt_shard(tmp_path):
    ws = _docs_ws(tmp_path, {"a.md": "# A\n\nalpha\n", "b.md": "# B\n\nbeta\n"})
    ws.remove("kb/raw/docs/a.md")                                 # docs WOULD be pruned …
    ws.remove("kb/raw/docs/a.md.txt")
    ws.write_text("graph/jira.json", "{ not valid json")         # … but a sibling shard is corrupt

    with pytest.raises(ValueError) as exc:
        maintain.reconcile(ws)
    assert "jira" in str(exc.value)
    # docs left fully intact — no half-applied sweep
    assert any(n.get("source_path") == "a.md" for n in navigate.load_shard(ws, "docs")["nodes"])


# --------------------------------------------------------------------------- #
# reconcile/forget/rename never touch a node that has no source_path (invariant 1)
# --------------------------------------------------------------------------- #
def test_reconcile_preserves_pathless_nodes(tmp_path):
    ws = _docs_ws(tmp_path, {"a.md": "# A\n\nalpha\n", "b.md": "# B\n\nbeta\n"})
    g = navigate.load_shard(ws, "docs")
    g["nodes"].append({"id": "rel/synthetic", "type": "relationship"})   # a node with NO source_path
    maintain._save_shard(ws, "docs", g)
    ws.remove("kb/raw/docs/a.md"); ws.remove("kb/raw/docs/a.md.txt")

    maintain.reconcile(ws)
    ids = {n["id"] for n in navigate.load_shard(ws, "docs")["nodes"]}
    assert "rel/synthetic" in ids                                 # pathless node survived
    assert not any(n.get("source_path") == "a.md" for n in navigate.load_shard(ws, "docs")["nodes"])


# --------------------------------------------------------------------------- #
# remove_source's raw listing is anchored to the directory — a sibling sharing the
# name prefix (kb/raw/docsX) is NOT swept up.
# --------------------------------------------------------------------------- #
def test_remove_source_anchors_prefix_to_directory(tmp_path):
    ws = _docs_ws(tmp_path, {"a.md": "# A\n\nalpha\n"})
    ws.write_text("kb/raw/docsX/keep.md", "SIBLING")             # shares the 'kb/raw/docs' prefix

    maintain.remove_source(ws, "docs")
    assert not ws.exists("kb/raw/docs/a.md")                     # the named source is gone
    assert ws.exists("kb/raw/docsX/keep.md")                     # the sibling is untouched


# --------------------------------------------------------------------------- #
# deep prune in ONE run: drops present-shard dangles, keeps absent + corrupt refs,
# and does not crash on a corrupt neighbor shard.
# --------------------------------------------------------------------------- #
def test_deep_prune_mixed_present_absent_empty_and_corrupt(tmp_path):
    ws = _docs_ws(tmp_path, {"a.md": "# A\n\nalpha\n"})
    did = _docfile(ws, "a.md")["id"]
    ws.write_text("graph/confluence.json", '{"nodes": [], "edges": []}')   # present but EMPTY
    ws.write_text("graph/mule.json", "{ corrupt")                          # present but CORRUPT
    cid = work.add_node(ws, "concept")
    work.link(ws, cid, f"docs:{did}", kind="about")              # valid present node → keep
    work.link(ws, cid, "docs:docfile/ghost", kind="about")      # present shard, missing node → DROP
    work.link(ws, cid, "confluence:page/x", kind="about")       # present-empty shard → DROP
    work.link(ws, cid, "salesforce:object/Account", kind="x")   # absent shard → keep
    work.link(ws, cid, "mule:flow/Y", kind="x")                 # corrupt shard → keep (no crash)

    dropped = work.prune_orphans(ws, deep=True)                  # must not raise
    assert dropped == 2                                          # exactly the two present-shard dangles
    survivors = {l["other"] for l in work.links_of(ws, cid)}
    assert survivors == {f"docs:{did}", "salesforce:object/Account", "mule:flow/Y"}


# --------------------------------------------------------------------------- #
# forget then a partial re-digest keeps the other file and does not resurrect the
# forgotten one (absence != deletion composes with forget)
# --------------------------------------------------------------------------- #
def test_idempotent_redigest_after_forget(tmp_path):
    ws = _docs_ws(tmp_path, {"a.md": "# A\n\nalpha\n", "b.md": "# B\n\nbeta\n"})
    maintain.forget(ws, "docs", "a.md")

    src2 = tmp_path / "docs2"; src2.mkdir()
    (src2 / "b.md").write_text("# B\n\nbeta\n", "utf-8")          # a tree WITHOUT a.md
    digest_to_tree(ws, "docs", str(src2))

    g = navigate.load_shard(ws, "docs")
    assert any(n.get("source_path") == "b.md" for n in g["nodes"])        # b kept
    assert not any(n.get("source_path") == "a.md" for n in g["nodes"])    # a not resurrected
