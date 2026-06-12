import json

import pytest

from librarian import LibrarianError
from factories import jira_ku, mule_ku, curated_ku


# ---- begin / rationale gate (I5) ----

def test_begin_requires_author(lib):
    with pytest.raises(LibrarianError):
        lib.begin("", "ingest first batch")


def test_begin_requires_real_rationale(lib):
    with pytest.raises(LibrarianError):
        lib.begin("dev", "fix")            # vague → rejected (I5)
    lib.begin("dev", "ingest first batch")  # ok


# ---- add + commit basics ----

def test_add_commit_persists_ku_file_and_changelog(lib, store):
    rep = lib.begin("dev", "ingest issue one").add_ku(jira_ku(1), body="hello").commit()
    assert rep.ok and rep.committed_generation == 1
    assert lib.get("jira:PROJ-1").title == "Issue 1"
    assert store.read("kb/raw/jira/PROJ-1.json") == b"hello"
    assert store.manifest_path.exists()
    assert len(lib.changelog.entries) == 1
    assert lib.changelog.entries[0].rationale == "ingest issue one"


def test_session_state_cleared_after_commit(lib, store):
    lib.begin("dev", "ingest issue one").add_ku(jira_ku(1)).commit()
    state = json.loads(store.session_path.read_text())
    assert state["pending"] == [] and state["author"] == ""


# ---- I3 stable ids ----

def test_add_existing_active_id_is_refused(lib):
    lib.begin("dev", "ingest issue one").add_ku(jira_ku(1)).commit()
    with pytest.raises(LibrarianError):
        lib.begin("dev", "try to re-add same id").add_ku(jira_ku(1)).commit()


# ---- I8 raw immutable / re-ingest path ----

def test_update_on_raw_is_refused(lib):
    lib.begin("dev", "ingest issue one").add_ku(jira_ku(1)).commit()
    with pytest.raises(LibrarianError):
        lib.begin("dev", "hand-edit raw").update_ku("jira:PROJ-1", title="nope").commit()


def test_reingest_unchanged_is_noop(lib):
    lib.begin("dev", "ingest issue one").ingest_ku(jira_ku(1), body="same").commit()
    gen_before = lib.manifest.generation
    rep = lib.begin("dev", "re-ingest identical content").ingest_ku(jira_ku(1), body="same").commit()
    assert rep.unchanged == ["jira:PROJ-1"]
    assert lib.manifest.generation == gen_before    # I9: no bump on no-op


def test_reingest_changed_replaces_and_bumps(lib):
    lib.begin("dev", "ingest issue one").ingest_ku(jira_ku(1), body="v1").commit()
    rep = lib.begin("dev", "re-ingest with new body").ingest_ku(jira_ku(1), body="v2").commit()
    assert rep.committed_generation == 2
    assert lib.read_body("jira:PROJ-1") == b"v2"


# ---- §9 staleness flagging ----

def test_reingest_flags_dependent_curated(lib):
    lib.begin("dev", "seed raw + curated").add_ku(jira_ku(1), body="v1") \
        .add_ku(curated_ku(derived_from="jira:PROJ-1"), body="note").commit()
    assert lib.get("curated:mappings/meter-map").review_needed is False
    lib.begin("dev", "re-ingest changed source").ingest_ku(jira_ku(1), body="v2").commit()
    assert lib.get("curated:mappings/meter-map").review_needed is True


def test_retire_flags_dependent_and_preserves_history(lib):
    lib.begin("dev", "seed raw + curated").add_ku(jira_ku(1), body="v1") \
        .add_ku(curated_ku(derived_from="jira:PROJ-1"), body="note").commit()
    lib.begin("dev", "retire the source ticket").retire_ku("jira:PROJ-1", "deleted at source").commit()
    src = lib.get("jira:PROJ-1")
    assert src is not None and src.status == "retired"      # never hard-deleted
    assert lib.get("curated:mappings/meter-map").review_needed is True


# ---- move / re-key (I3 + I6) ----

def test_move_repoints_inbound_links(lib):
    lib.begin("dev", "seed mule flow and a mapping").add_ku(mule_ku("oldName"), body="<flow/>") \
        .add_ku(curated_ku(derived_from="mule:oldName"), body="n").commit()
    lib.begin("dev", "rename the mule flow").move_ku(
        "mule:oldName", "kb/raw/mule/newName.xml", new_id="mule:newName").commit()
    assert lib.get("mule:oldName") is None
    assert lib.get("mule:newName").path == "kb/raw/mule/newName.xml"
    link = lib.get("curated:mappings/meter-map").links[0]
    assert link["to"] == "mule:newName"          # I6: inbound link re-pointed


# ---- I6 orphan links rejected ----

def test_orphan_derived_from_is_rejected(lib):
    rep = lib.begin("dev", "add curated note pointing nowhere") \
        .add_ku(curated_ku(derived_from="jira:DOES-NOT-EXIST"), body="n").preview()
    assert not rep.ok
    assert any("orphan link" in e for e in rep.errors)


# ---- I12 atomic rollback ----

def test_failing_op_rolls_back_whole_transaction(lib, store):
    lib.begin("dev", "seed one ticket").add_ku(jira_ku(1), body="v1").commit()
    gen_before = lib.manifest.generation
    # good op + bad op (orphan) in one txn → commit must refuse, nothing applied
    txn = lib.begin("dev", "add a good and a bad ku") \
        .add_ku(jira_ku(2), body="good") \
        .add_ku(curated_ku(derived_from="jira:NOPE"), body="bad")
    with pytest.raises(LibrarianError):
        txn.commit()
    assert lib.get("jira:PROJ-2") is None                 # good op not applied
    assert lib.manifest.generation == gen_before          # generation not bumped
    assert not store.exists("kb/raw/jira/PROJ-2.json")     # no file written


# ---- stats end-to-end ----

def test_stats_after_several_commits(lib):
    lib.begin("dev", "ingest two issues and a note") \
        .add_ku(jira_ku(1)).add_ku(jira_ku(2)).add_ku(curated_ku()).commit()
    s = lib.stats()
    assert s["total"] == 3
    assert s["by_status"]["active"] == 3
    assert s["generation"] == 1


# ---- report rendering cap (sandbox stdout truncates long output) ----

def test_huge_report_repr_is_capped():
    """A 1000-change report renders in well under ~30 lines: exact counts in
    the header, at most 5 examples per change kind + '... and N more'."""
    from librarian.librarian import Report

    rep = Report()
    rep.changes = ([f"INGEST jira:PROJ-{i} (new)" for i in range(900)]
                   + [f"RE-INGEST salesforce:object/Obj{i} (content changed)"
                      for i in range(100)])
    rep.unchanged = [f"mule:flows/file{i}.xml" for i in range(500)]
    text = repr(rep)
    lines = text.splitlines()
    assert len(lines) <= 30
    assert "1000 change(s), 500 unchanged" in lines[0]   # counts stay exact
    assert "... and 895 more INGEST" in text
    assert "... and 95 more RE-INGEST" in text
    assert "... and 495 more unchanged" in text
    assert "jira:PROJ-0" in text and "jira:PROJ-6" not in text   # 5 examples


def test_small_report_repr_lists_everything():
    from librarian.librarian import Report

    rep = Report()
    rep.changes = [f"ADD curated:notes/n{i} (curated-note, curated)" for i in range(3)]
    text = repr(rep)
    assert all(f"curated:notes/n{i}" in text for i in range(3))
    assert "more" not in text                            # no cap line under the cap
