"""Search index (entity bridge + FTS) and the retrieve primitives."""
from librarian import Librarian, Store, KnowledgeUnit, rebuild_indexes, retrieve


def _sf_class(name, entities):
    return KnowledgeUnit(id=f"salesforce:apexclass/{name}", kind="source-record", tier="raw",
                         source="salesforce", path=f"kb/raw/salesforce/classes/{name}.cls",
                         title=name, entities=entities)


def _jira(num, title, entities):
    return KnowledgeUnit(id=f"jira:PROJ-{num}", kind="source-record", tier="raw", source="jira",
                         path=f"kb/raw/jira/PROJ-{num}.json", title=title, entities=entities)


def seed(tmp_path):
    lib = Librarian(Store(tmp_path / "mem"))
    lib.begin("dev", "seed two sources sharing an entity") \
        .add_ku(_sf_class("MeterPointService", ["MeterPointService", "MeterPoint__c"]),
                body="public class MeterPointService { /* bulk import retry logic */ }") \
        .add_ku(_jira(1, "Meter sync fails on bulk import",
                      ["MeterPointService", "PROJ-1"]),
                body="The bulk import retry fails when MeterPointService is called twice.") \
        .commit()
    return lib


def test_entity_bridge_joins_across_sources(tmp_path):
    lib = seed(tmp_path)
    rebuild_indexes(lib, "dev", "build the search index")
    con = retrieve.open_index(lib)

    hits = retrieve.find_entity(con, "MeterPointService")
    ids = {h["ku_id"] for h in hits}
    assert ids == {"salesforce:apexclass/MeterPointService", "jira:PROJ-1"}

    xs = retrieve.cross_source(con, "MeterPointService")
    assert set(xs) == {"salesforce", "jira"}
    assert xs["jira"] == ["jira:PROJ-1"]


def test_entity_lookup_is_case_insensitive(tmp_path):
    lib = seed(tmp_path)
    rebuild_indexes(lib, "dev", "build index")
    con = retrieve.open_index(lib)
    assert retrieve.find_entity(con, "meterpointservice")
    assert "MeterPoint__c" in retrieve.entity_like(con, "meterpoint")


def test_fts_search_ranks_and_snippets(tmp_path):
    lib = seed(tmp_path)
    rebuild_indexes(lib, "dev", "build index")
    con = retrieve.open_index(lib)

    res = retrieve.search(con, "bulk import retry")
    assert res, "expected FTS hits"
    ids = {r["ku_id"] for r in res}
    assert "jira:PROJ-1" in ids
    assert any("[" in r["snippet"] for r in res)         # snippet markers present

    # source filter
    only_sf = retrieve.search(con, "bulk import", source="salesforce")
    assert all(r["source"] == "salesforce" for r in only_sf)


def test_rebuild_is_idempotent(tmp_path):
    lib = seed(tmp_path)
    rebuild_indexes(lib, "dev", "build index")
    gen = lib.manifest.generation
    rep = rebuild_indexes(lib, "dev", "rebuild unchanged index")
    assert rep.unchanged == ["agent:index/search"]
    assert lib.manifest.generation == gen                # no churn when KB unchanged


def test_index_updates_after_new_ku(tmp_path):
    lib = seed(tmp_path)
    rebuild_indexes(lib, "dev", "build index")
    lib.begin("dev", "add a second jira ticket").add_ku(
        _jira(2, "Confluence sync", ["MeterPointService"]), body="another meter ticket").commit()
    rebuild_indexes(lib, "dev", "rebuild after new ticket")
    con = retrieve.open_index(lib)
    assert len(retrieve.find_entity(con, "MeterPointService")) == 3
