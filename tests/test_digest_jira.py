"""Jira digest (graph-builder-backed adapter) — synthetic collector dumps.

Fixture mirrors the collector layout (``<dump_dir>/<PROJECT>/<KEY>.issue.json``,
one Data Center REST envelope per file): a task with a link/label/users, a bare
bug, and a subtask. Reproducible without any real Jira; fictional Acme data only.
"""
import json

from librarian import Librarian, Store, rebuild_indexes, retrieve
from librarian.digest import jira


def issue_json(key, project, summary, description="", issue_type="Task",
               status="Open", **extra):
    fields = {
        "project": {"key": project, "name": "Acme Billing"},
        "summary": summary,
        "description": description,
        "issuetype": {"name": issue_type},
        "status": {"name": status},
        "updated": "2026-06-01T10:00:00.000+0000",
    }
    fields.update(extra)
    return json.dumps({"key": key, "id": "10001", "fields": fields})


def make_jira_dump(root):
    proj = root / "jira-dump" / "ACME"
    proj.mkdir(parents=True)
    (proj / "ACME-101.issue.json").write_text(issue_json(
        "ACME-101", "ACME", "Nightly invoice export fails for large batches",
        description="The invoice export aborts after 5000 rows. [~jdoe] please "
                    "check the syncBilling flow timeout.",
        labels=["billing"],
        assignee={"name": "jdoe"}, reporter={"name": "rroe"},
        issuelinks=[{"type": {"name": "blocks"},
                     "outwardIssue": {"key": "ACME-102"}}],
        subtasks=[{"key": "ACME-103"}],
    ), "utf-8")
    (proj / "ACME-102.issue.json").write_text(issue_json(
        "ACME-102", "ACME", "Retry queue grows unbounded", issue_type="Bug",
        status="Done"), "utf-8")
    (proj / "ACME-103.issue.json").write_text(issue_json(
        "ACME-103", "ACME", "Add export timeout metric", issue_type="Sub-task",
        parent={"key": "ACME-101"}), "utf-8")
    return root / "jira-dump"


def test_parse_issues_and_summary(tmp_path):
    d = jira.parse_jira(make_jira_dump(tmp_path))
    assert {i.key for i in d.issues} == {"ACME-101", "ACME-102", "ACME-103"}
    assert all(i.project_key == "ACME" for i in d.issues)
    assert d.errors == [] and d.unresolved == []
    s = d.summary()
    assert s["issues"] == 3 and s["projects"] == 1
    # 3 issues + 1 project + 1 label + 2 users
    assert s["nodes"] == 7
    # 101: child-of/links-to x2/labeled/assigned-to/authored-by/mentions = 7;
    # 102: child-of = 1; 103: child-of parent = 1
    assert s["edges"] == 9


def test_ingest_creates_kus_and_graph(tmp_path):
    lib = Librarian(Store(tmp_path / "mem"))
    rep, d = jira.ingest_jira(lib, make_jira_dump(tmp_path), "dev",
                              "ingest sample Jira dump")
    assert rep.ok
    ku = lib.get("jira:ACME/ACME-101")
    assert ku is not None and ku.tier == "raw" and ku.kind == "source-record"
    assert ku.title == "Nightly invoice export fails for large batches"
    assert ku.path == "kb/raw/jira/ACME/ACME-101.json"
    assert ku.provenance == {
        "project_key": "ACME", "issue_type": "Task", "status": "Open",
        "updated": "2026-06-01T10:00:00.000+0000",
        "source_path": "ACME/ACME-101.issue.json",
    }
    assert lib.get("jira:ACME/ACME-102") is not None
    assert lib.get("jira:ACME/ACME-103") is not None
    gku = lib.get(jira.GRAPH_ID)
    assert gku is not None and gku.tier == "structured" and gku.kind == "graph"
    g = jira.load_graph(lib)
    assert len(g["nodes"]) == 7 and len(g["edges"]) == 9
    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    assert ("jiraissue/ACME-101", "links-to", "jiraissue/ACME-102") in edges
    assert ("jiraissue/ACME-103", "child-of", "jiraissue/ACME-101") in edges


def test_entities_are_structured_ids_only(tmp_path):
    """HARD RULE: the entity bridge gets the issue KEY only — never the summary
    or any prose-derived name."""
    lib = Librarian(Store(tmp_path / "mem"))
    jira.ingest_jira(lib, make_jira_dump(tmp_path), "dev", "ingest sample Jira dump")
    for key in ("ACME-101", "ACME-102", "ACME-103"):
        assert lib.get(f"jira:ACME/{key}").entities == [key]


def test_graph_body_redacts_description_text(tmp_path):
    """Issue bodies live in the raw KUs; the stored graph JSON must carry no
    inline ``text`` attr — and no cross-source ``documents`` edges (no join)."""
    lib = Librarian(Store(tmp_path / "mem"))
    jira.ingest_jira(lib, make_jira_dump(tmp_path), "dev", "ingest sample Jira dump")
    stored = json.loads(lib.read_body(jira.GRAPH_ID))
    assert all("text" not in n for n in stored["nodes"])
    redacted = [n["id"] for n in stored["nodes"] if n.get("text_redacted")]
    assert "jiraissue/ACME-101" in redacted          # it had a description
    assert all(e["type"] != "documents" for e in stored["edges"])


def test_reingest_unchanged_is_noop(tmp_path):
    lib = Librarian(Store(tmp_path / "mem"))
    dump = make_jira_dump(tmp_path)
    jira.ingest_jira(lib, dump, "dev", "ingest sample Jira dump")
    gen = lib.manifest.generation
    rep, _ = jira.ingest_jira(lib, dump, "dev", "re-ingest identical Jira dump")
    assert rep.unchanged and lib.manifest.generation == gen


def test_cross_batch_reference_keeps_the_real_node(tmp_path):
    """A second dump that references an issue from the FIRST dump parses that
    issue as an ``external`` stub (it is absent from the standalone re-parse).
    The merge must keep the earlier REAL node — never downgrade it to the stub —
    and re-ingesting the second dump unchanged must be a no-op (no oscillation)."""
    lib = Librarian(Store(tmp_path / "mem"))
    jira.ingest_jira(lib, make_jira_dump(tmp_path), "dev", "ingest sample Jira dump")

    d2 = tmp_path / "jira-dump-2" / "ACME"
    d2.mkdir(parents=True)
    (d2 / "ACME-200.issue.json").write_text(issue_json(
        "ACME-200", "ACME", "Follow-up on the export bug",
        issuelinks=[{"type": {"name": "relates"},
                     "outwardIssue": {"key": "ACME-101"}}]), "utf-8")
    jira.ingest_jira(lib, tmp_path / "jira-dump-2", "dev",
                     "ingest a second jira dump referencing ACME-101")

    g = jira.load_graph(lib)
    a101 = next(n for n in g["nodes"] if n["id"] == "jiraissue/ACME-101")
    assert not a101.get("external")                       # stayed real, not a stub
    assert any(n["id"] == "jiraissue/ACME-200" for n in g["nodes"])
    assert any(e["src"] == "jiraissue/ACME-200" and e["dst"] == "jiraissue/ACME-101"
               for e in g["edges"])                       # the cross-batch link formed

    gen = lib.manifest.generation                          # re-ingest batch 2 unchanged
    rep, _ = jira.ingest_jira(lib, tmp_path / "jira-dump-2", "dev",
                              "re-ingest the second jira dump unchanged")
    assert lib.manifest.generation == gen and jira.GRAPH_ID in rep.unchanged


def test_fts_finds_issue_by_body_word_and_read_body(tmp_path):
    """Jira prose is reached via full-text search (never the entity bridge), and
    ``read_body`` returns the full raw issue detail."""
    lib = Librarian(Store(tmp_path / "mem"))
    jira.ingest_jira(lib, make_jira_dump(tmp_path), "dev", "ingest sample Jira dump")
    rebuild_indexes(lib, "dev", "rebuild indexes after jira digest")
    con = retrieve.open_index(lib)
    hits = {h["ku_id"] for h in retrieve.search(con, "syncBilling")}
    assert "jira:ACME/ACME-101" in hits
    detail = json.loads(lib.read_body("jira:ACME/ACME-101"))
    assert detail["fields"]["description"].startswith("The invoice export aborts")
    # the summary is NOT an entity — prose stays out of the bridge
    assert retrieve.find_entity(con, "Nightly invoice export fails for large batches") == []
    assert {h["ku_id"] for h in retrieve.find_entity(con, "ACME-101")} \
        == {"jira:ACME/ACME-101"}
