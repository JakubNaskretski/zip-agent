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


def test_open_index_builds_from_live_files_without_rebuild(tmp_path):
    """open_index no longer needs a persisted index: it builds the MemIndex
    from the live KB, so a seeded-but-never-rebuilt lib resolves immediately."""
    lib = seed(tmp_path)                       # seeded, but never indexed
    con = retrieve.open_index(lib)             # builds in memory — never raises
    assert retrieve.find_entity(con, "MeterPointService")


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

    res = retrieve.search(con, "bulk import retry", lib=lib)
    assert res, "expected FTS hits"
    ids = {r["ku_id"] for r in res}
    assert "jira:PROJ-1" in ids
    # snippets are match-positioned excerpts read from the KU bodies on demand
    assert any("retry" in r["snippet"].lower() for r in res)
    # without lib the contentless index has no text to quote
    assert all(r["snippet"] == "" for r in retrieve.search(con, "bulk import retry"))

    # source filter
    only_sf = retrieve.search(con, "bulk import", source="salesforce")
    assert all(r["source"] == "salesforce" for r in only_sf)


def test_rebuild_is_a_noop(tmp_path):
    """rebuild_indexes is now a no-op kept for compatibility: it returns an ok
    Report and never bumps the manifest generation (search is built fresh at
    open_index from the live files)."""
    lib = seed(tmp_path)
    gen = lib.manifest.generation
    rep = rebuild_indexes(lib, "dev", "rebuild — now a no-op")
    assert rep.ok
    assert lib.manifest.generation == gen                # no churn — nothing persisted


def test_index_updates_after_new_ku(tmp_path):
    lib = seed(tmp_path)
    rebuild_indexes(lib, "dev", "build index")
    lib.begin("dev", "add a second jira ticket").add_ku(
        _jira(2, "Confluence sync", ["MeterPointService"]), body="another meter ticket").commit()
    rebuild_indexes(lib, "dev", "rebuild after new ticket")
    con = retrieve.open_index(lib)
    assert len(retrieve.find_entity(con, "MeterPointService")) == 3
