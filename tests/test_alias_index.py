"""Alias index + resolve_name tests.

Covers:
  - mech alias generation (CamelCase, __c/__r strip, acronym, no-acronym for
    single-word names, __r handling)
  - label aliases from sf/mule graph KUs only (jira/confluence/docs excluded)
  - curated glossary KUs (comment skip, works for canonical not in entities)
  - resolve_name: ranking by KU count, collision, exact wins, unknown → [],
    limit respected, round-trip through rebuild_indexes
  - rebuild regenerates aliases (I13)
"""
import json

import pytest

from librarian import KnowledgeUnit, Librarian, Store, rebuild_indexes, retrieve
from librarian.index import _mech_aliases, _norm, build_index, load_sqlite


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sf_ku(name, entities=None, **kw):
    d = dict(
        id=f"salesforce:apexclass/{name}",
        kind="source-record", tier="raw", source="salesforce",
        path=f"kb/raw/salesforce/classes/{name}.cls",
        title=name,
        entities=entities if entities is not None else [name],
        confidence="VERIFIED",
    )
    d.update(kw)
    return KnowledgeUnit(**d)


def _sf_object_ku(name, entities=None):
    return KnowledgeUnit(
        id=f"salesforce:object/{name}",
        kind="source-record", tier="raw", source="salesforce",
        path=f"kb/raw/salesforce/objects/{name}.object-meta.xml",
        title=name,
        entities=entities if entities is not None else [name],
        confidence="VERIFIED",
    )


def _graph_ku(graph_id, graph_path, graph_data):
    """A structured graph KU with the given payload."""
    return KnowledgeUnit(
        id=graph_id, kind="graph", tier="structured",
        source=graph_id.split(":")[0],
        path=graph_path,
        title=graph_id, confidence="VERIFIED",
    ), json.dumps({"version": 1, "nodes": graph_data["nodes"],
                   "edges": graph_data.get("edges", []),
                   "unresolved": [], "errors": []}, indent=2)


def _glossary_ku(slug, canonicals, body):
    return KnowledgeUnit(
        id=f"curated:glossary/{slug}",
        kind="curated-note", tier="curated", source="agent",
        path=f"kb/curated/glossary/{slug}.md",
        title=slug, entities=canonicals, confidence="VERIFIED",
    ), body


def _lib(tmp_path):
    return Librarian(Store(tmp_path / "mem"))


def _con_after_rebuild(lib):
    rebuild_indexes(lib, "dev", "build alias index")
    return retrieve.open_index(lib)


def _aliases_for(con, canonical):
    """Return {alias: via} for a canonical from the aliases table."""
    rows = con.execute(
        "SELECT alias, via FROM aliases WHERE canonical=?", (canonical,)
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _resolve(con, text, limit=10):
    return retrieve.resolve_name(con, text, limit=limit)


# ===========================================================================
# 1. Unit tests for _mech_aliases (pure function, no lib needed)
# ===========================================================================

class TestMechAliases:
    def _set(self, name):
        return set(_mech_aliases(name))

    def test_camel_split_service_point(self):
        aliases = self._set("ServicePoint__c")
        assert "service point" in aliases

    def test_camel_split_no_suffix(self):
        aliases = self._set("ServicePoint")
        assert "service point" in aliases

    def test_servicepoint_joined(self):
        """No-space join of spaced words."""
        aliases = self._set("ServicePoint__c")
        assert "servicepoint" in aliases

    def test_acronym_two_words(self):
        """sp acronym for ServicePoint."""
        aliases = self._set("ServicePoint__c")
        assert "sp" in aliases

    def test_acronym_three_words(self):
        aliases = self._set("ServicePointHistory")
        assert "sph" in aliases
        assert "service point history" in aliases

    def test_single_word_no_acronym(self):
        """Account has one word — no acronym alias."""
        aliases = self._set("Account")
        # 'a' would be a 1-char alias — must not appear
        assert "a" not in aliases

    def test_stripped_form_in_aliases(self):
        """ServicePoint__c stripped → ServicePoint → further variants."""
        aliases = self._set("ServicePoint__c")
        # stripped baseline itself is separate form
        assert "servicepoint" in aliases

    def test_r_suffix_stripped(self):
        """__r suffix is stripped (lookup relationship names)."""
        aliases = self._set("MeterPoint__r")
        assert "meter point" in aliases
        assert "mp" in aliases

    def test_meter_point_camel(self):
        aliases = self._set("MeterPoint__c")
        assert "meter point" in aliases
        assert "meterpoint" in aliases
        assert "mp" in aliases

    def test_exact_lowercase_not_emitted(self):
        """Alias identical to lower(canonical) must not appear (entities.name_norm covers it)."""
        canonical = "sp"
        aliases = self._set(canonical)
        assert "sp" not in aliases   # exact lowercase — must skip

    def test_empty_and_single_char_dropped(self):
        """No empty or single-character aliases ever emitted."""
        for name in ("A", "x__c", "Z__r"):
            for alias in _mech_aliases(name):
                assert len(alias) > 1, f"single-char alias {alias!r} from {name!r}"

    def test_norm_helper(self):
        assert _norm("  Service  Point  ") == "service point"
        assert _norm("METER_POINT") == "meter_point"


# ===========================================================================
# 2. Integration: mech aliases in the built index
# ===========================================================================

class TestMechIntegration:
    def _seed(self, tmp_path, names):
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed entities for alias test")
        for name in names:
            txn.add_ku(_sf_object_ku(name), body=f"object {name}")
        txn.commit()
        return lib

    def test_service_point_variants_resolve(self, tmp_path):
        lib = self._seed(tmp_path, ["ServicePoint__c"])
        con = _con_after_rebuild(lib)
        for text in ("service point", "servicepoint", "sp"):
            hits = _resolve(con, text)
            assert hits, f"no hits for {text!r}"
            names = [h["name"] for h in hits]
            assert "ServicePoint__c" in names, f"ServicePoint__c not in hits for {text!r}"

    def test_meter_point_variants_resolve(self, tmp_path):
        lib = self._seed(tmp_path, ["MeterPoint__c"])
        con = _con_after_rebuild(lib)
        for text in ("meter point", "meterpoint", "mp"):
            hits = _resolve(con, text)
            assert hits, f"no hits for {text!r}"
            assert any(h["name"] == "MeterPoint__c" for h in hits)

    def test_single_word_entity_no_acronym(self, tmp_path):
        """Account is single-word: 'a' must never appear as an alias."""
        lib = self._seed(tmp_path, ["Account"])
        con = _con_after_rebuild(lib)
        # 'a' would be a 1-char alias — must be absent
        aliases = _aliases_for(con, "Account")
        single_char = [a for a in aliases if len(a) == 1]
        assert not single_char, f"unexpected 1-char aliases: {single_char}"

    def test_r_suffix_alias(self, tmp_path):
        lib = self._seed(tmp_path, ["MeterPoint__r"])
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "meter point")
        assert any(h["name"] == "MeterPoint__r" for h in hits)


# ===========================================================================
# 3. Label aliases from graph KUs
# ===========================================================================

class TestLabelAliases:
    def _build_lib(self, tmp_path, extra_nodes=None, graph_id="salesforce:graph/sf",
                    graph_path="kb/structured/salesforce/graph.json",
                    entity_name="MeterPoint__c"):
        """Seed a lib with one entity KU and a graph KU whose node has labels."""
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed sf entity and graph")
        txn.add_ku(_sf_object_ku(entity_name), body=f"object {entity_name}")
        nodes = extra_nodes if extra_nodes is not None else [
            {"id": f"object/{entity_name}", "type": "object", "label": "Meter Point",
             "label_pl": "Punkt Poboru"},
        ]
        gku, gbody = _graph_ku(graph_id, graph_path, {"nodes": nodes})
        txn.ingest_ku(gku, body=gbody)
        txn.commit()
        return lib

    def test_label_resolves(self, tmp_path):
        lib = self._build_lib(tmp_path)
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "meter point")
        assert any(h["name"] == "MeterPoint__c" for h in hits)

    def test_label_pl_resolves(self, tmp_path):
        lib = self._build_lib(tmp_path)
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "punkt poboru")
        assert any(h["name"] == "MeterPoint__c" for h in hits)
        # via should be label
        via_values = {h["via"] for h in hits if h["name"] == "MeterPoint__c"}
        assert "label" in via_values

    def test_label_on_unknown_canonical_not_harvested(self, tmp_path):
        """A node whose canonical is NOT in entities must not create an alias."""
        lib = _lib(tmp_path)
        nodes = [
            # NotInEntities__c is NOT added as a KU — it is graph-only
            {"id": "object/NotInEntities__c", "type": "object", "label": "Ghost Object"},
        ]
        gku, gbody = _graph_ku("salesforce:graph/sf",
                               "kb/structured/salesforce/graph.json", {"nodes": nodes})
        txn = lib.begin("dev", "seed graph with orphan node")
        txn.ingest_ku(gku, body=gbody)
        txn.commit()
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "ghost object")
        assert not hits, "label for non-entity node must not resolve"

    def test_jira_graph_labels_not_harvested(self, tmp_path):
        """A jira graph KU (not in _LABEL_GRAPH_IDS) must not feed aliases."""
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed jira graph")
        # ingest a KU with an entity so the canonical would be available
        txn.add_ku(_sf_object_ku("MeterPoint__c"), body="obj")
        # Create a "jira-like" graph KU — not in the two allowed IDs
        jira_gku = KnowledgeUnit(
            id="jira:graph/jira",
            kind="graph", tier="structured", source="jira",
            path="kb/structured/jira/graph.json",
            title="Jira graph", confidence="VERIFIED",
        )
        jira_body = json.dumps({"version": 1, "nodes": [
            {"id": "issue/PROJ-1", "type": "issue", "label": "Secret Alias For MeterPoint"},
        ], "edges": [], "unresolved": [], "errors": []})
        txn.ingest_ku(jira_gku, body=jira_body)
        txn.commit()
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "secret alias for meterpoint")
        assert not hits, "jira graph labels must not be harvested"

    def test_docs_graph_labels_not_harvested(self, tmp_path):
        """A docs graph KU must not feed aliases."""
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed docs graph")
        txn.add_ku(_sf_object_ku("MeterPoint__c"), body="obj")
        docs_gku = KnowledgeUnit(
            id="docs:graph/docs",
            kind="graph", tier="structured", source="docs",
            path="kb/structured/docs/graph.json",
            title="Docs graph", confidence="VERIFIED",
        )
        docs_body = json.dumps({"version": 1, "nodes": [
            {"id": "doc/report", "type": "doc", "label": "Exclusive Docs Label"},
        ], "edges": [], "unresolved": [], "errors": []})
        txn.ingest_ku(docs_gku, body=docs_body)
        txn.commit()
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "exclusive docs label")
        assert not hits, "docs graph labels must not be harvested"

    def test_mule_graph_label_resolves(self, tmp_path):
        """Labels from mule:graph/mule ARE harvested."""
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed mule entity and graph")
        mule_ku = KnowledgeUnit(
            id="mule:syncMeterPoint.xml", kind="source-record", tier="raw",
            source="mule", path="kb/raw/mule/syncMeterPoint.xml",
            title="syncMeterPoint.xml", entities=["syncMeterPoint"],
            confidence="VERIFIED",
        )
        txn.add_ku(mule_ku, body="<flow/>")
        mule_gku = KnowledgeUnit(
            id="mule:graph/mule", kind="graph", tier="structured", source="mule",
            path="kb/structured/mule/graph.json",
            title="Mule graph", confidence="VERIFIED",
        )
        mule_body = json.dumps({"version": 1, "nodes": [
            {"id": "muleflow/syncMeterPoint", "type": "muleflow",
             "label": "Sync Meter Point"},
        ], "edges": [], "unresolved": [], "errors": []})
        txn.ingest_ku(mule_gku, body=mule_body)
        txn.commit()
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "sync meter point")
        assert any(h["name"] == "syncMeterPoint" for h in hits)


# ===========================================================================
# 4. Curated glossary aliases
# ===========================================================================

class TestCuratedAliases:
    def _seed_with_glossary(self, tmp_path, canonicals, body, with_entity=True):
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed glossary")
        if with_entity:
            for c in canonicals:
                txn.add_ku(_sf_object_ku(c), body=f"obj {c}")
        gku, gbody = _glossary_ku("meter-aliases", canonicals, body)
        txn.add_ku(gku, body=gbody)
        txn.commit()
        return lib

    def test_body_lines_resolve_to_canonical(self, tmp_path):
        lib = self._seed_with_glossary(tmp_path, ["MeterPoint__c"],
                                       "meter point\nMP\npunkt pomiaru\n")
        con = _con_after_rebuild(lib)
        for text in ("meter point", "mp", "punkt pomiaru"):
            hits = _resolve(con, text)
            assert any(h["name"] == "MeterPoint__c" for h in hits), \
                f"curated alias {text!r} did not resolve"

    def test_comment_lines_skipped(self, tmp_path):
        lib = self._seed_with_glossary(tmp_path, ["MeterPoint__c"],
                                       "# this is a comment\nmeter point\n")
        con = _con_after_rebuild(lib)
        # comment text must not become an alias
        hits = _resolve(con, "this is a comment")
        assert not hits
        # but the actual alias must work
        assert _resolve(con, "meter point")

    def test_empty_lines_skipped(self, tmp_path):
        lib = self._seed_with_glossary(tmp_path, ["MeterPoint__c"],
                                       "\n\nmeter point\n\n")
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "meter point")
        assert hits

    def test_canonical_not_in_entities_still_works(self, tmp_path):
        """Glossary canonical does NOT need to be in entities (curated is trusted)."""
        lib = _lib(tmp_path)
        # no entity KU for FutureObject__c — only the glossary
        txn = lib.begin("dev", "seed glossary only")
        gku, gbody = _glossary_ku("future-aliases", ["FutureObject__c"],
                                  "future object\nfo\n")
        txn.add_ku(gku, body=gbody)
        txn.commit()
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "future object")
        assert any(h["name"] == "FutureObject__c" for h in hits)

    def test_multiple_canonicals_from_one_glossary(self, tmp_path):
        """One glossary KU may have multiple canonicals — each alias applies to all."""
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed multi-canonical glossary")
        txn.add_ku(_sf_object_ku("Alpha__c"), body="a")
        txn.add_ku(_sf_object_ku("Beta__c"), body="b")
        gku, gbody = _glossary_ku("ab-aliases", ["Alpha__c", "Beta__c"],
                                  "shared alias\n")
        txn.add_ku(gku, body=gbody)
        txn.commit()
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "shared alias")
        names = {h["name"] for h in hits}
        assert "Alpha__c" in names and "Beta__c" in names


# ===========================================================================
# 5. resolve_name: ranking, exact, unknown, limit, round-trip
# ===========================================================================

class TestResolveName:
    def _seed_two_entities(self, tmp_path):
        """Two entities share a mech alias collision (both start with SP):
        ServicePoint__c (in 3 KUs) vs ServiceProvider (in 1 KU).
        Both produce 'sp' as a mech alias; the one with more KUs ranks higher.
        """
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed two entities sharing sp alias")
        # ServicePoint__c in 3 KUs
        for i in range(3):
            ku = KnowledgeUnit(
                id=f"salesforce:apexclass/SpUser{i}",
                kind="source-record", tier="raw", source="salesforce",
                path=f"kb/raw/salesforce/classes/SpUser{i}.cls",
                title=f"SpUser{i}",
                entities=["ServicePoint__c"],
                confidence="VERIFIED",
            )
            txn.add_ku(ku, body=f"class SpUser{i}")
        # ServiceProvider in 1 KU
        txn.add_ku(_sf_ku("ServiceProvider", entities=["ServiceProvider"]),
                   body="class ServiceProvider")
        txn.commit()
        return lib

    def test_ranking_by_ku_count(self, tmp_path):
        lib = self._seed_two_entities(tmp_path)
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "sp")
        assert len(hits) >= 2
        names = [h["name"] for h in hits]
        # ServicePoint__c has more KUs — must rank first
        assert names[0] == "ServicePoint__c", f"unexpected order: {names}"

    def test_collision_returns_both(self, tmp_path):
        lib = self._seed_two_entities(tmp_path)
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "sp")
        names = {h["name"] for h in hits}
        assert "ServicePoint__c" in names
        assert "ServiceProvider" in names

    def test_exact_name_wins_via_exact(self, tmp_path):
        """An exact name match via entities.name_norm returns via='exact'."""
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed entity")
        txn.add_ku(_sf_object_ku("MeterPoint__c"), body="obj")
        txn.commit()
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "meterpoint__c")
        assert hits
        exact_hits = [h for h in hits if h["name"] == "MeterPoint__c"]
        assert exact_hits
        assert exact_hits[0]["via"] == "exact"

    def test_unknown_returns_empty(self, tmp_path):
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "empty seed")
        txn.add_ku(_sf_object_ku("Account"), body="obj")
        txn.commit()
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "absolutely no such thing xyz123")
        assert hits == []

    def test_limit_respected(self, tmp_path):
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed many service entities")
        # Give several entities the same mech alias 'sp' by picking names
        # that produce that acronym: ServicePoint, ServiceProvider, SmartProduct, ...
        names = ["ServicePoint__c", "ServiceProvider", "SmartProduct",
                 "SupplyPoint__c", "SystemProcess"]
        for n in names:
            txn.add_ku(_sf_object_ku(n), body=f"obj {n}")
        txn.commit()
        con = _con_after_rebuild(lib)
        # limit=2 should return at most 2
        hits = _resolve(con, "sp", limit=2)
        assert len(hits) <= 2

    def test_round_trip_through_serialize(self, tmp_path):
        """Aliases survive the serialize/deserialize round-trip (SQLite bytes KU)."""
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed for round-trip")
        txn.add_ku(_sf_object_ku("ServicePoint__c"), body="obj")
        txn.commit()
        rebuild_indexes(lib, "dev", "build index for round-trip test")
        # open_index loads via lib.read_body + load_sqlite (the real path)
        con = retrieve.open_index(lib)
        hits = _resolve(con, "service point")
        assert any(h["name"] == "ServicePoint__c" for h in hits)

    def test_empty_text_returns_empty(self, tmp_path):
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed entity for empty-query test")
        txn.add_ku(_sf_object_ku("Account"), body="obj")
        txn.commit()
        con = _con_after_rebuild(lib)
        assert _resolve(con, "") == []
        assert _resolve(con, "   ") == []

    def test_normalizes_input_whitespace(self, tmp_path):
        """Extra whitespace in query is collapsed before lookup."""
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed entity for whitespace normalization test")
        txn.add_ku(_sf_object_ku("ServicePoint__c"), body="obj")
        txn.commit()
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "  service   point  ")
        assert any(h["name"] == "ServicePoint__c" for h in hits)

    def test_via_priority_exact_over_mech(self, tmp_path):
        """When a name resolves both via exact and mech, exact wins."""
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed sp entity for via priority test")
        # Add entity 'sp' — exact match — and ServicePoint__c — mech 'sp' alias
        txn.add_ku(_sf_object_ku("sp"), body="obj")
        txn.add_ku(_sf_object_ku("ServicePoint__c"), body="obj")
        txn.commit()
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "sp")
        by_name = {h["name"]: h for h in hits}
        # 'sp' itself resolves via exact
        assert "sp" in by_name
        assert by_name["sp"]["via"] == "exact"

    def test_curated_ranks_over_mech_same_name(self, tmp_path):
        """Curated via ranks higher than mech via for the same canonical."""
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed curated over mech")
        txn.add_ku(_sf_object_ku("MeterPoint__c"), body="obj")
        # glossary explicitly declares 'mp' as an alias → curated
        gku, gbody = _glossary_ku("mp-glossary", ["MeterPoint__c"], "mp\n")
        txn.add_ku(gku, body=gbody)
        txn.commit()
        con = _con_after_rebuild(lib)
        hits = _resolve(con, "mp")
        assert hits
        mp_hit = next((h for h in hits if h["name"] == "MeterPoint__c"), None)
        assert mp_hit is not None
        # curated is preferred over mech (both would match 'mp')
        assert mp_hit["via"] == "curated"


# ===========================================================================
# 6. Rebuild regenerates aliases (I13)
# ===========================================================================

class TestRebuildRegeneratesAliases:
    def test_aliases_absent_before_rebuild(self, tmp_path):
        """Before rebuild_indexes, the index KU doesn't even exist."""
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed only")
        txn.add_ku(_sf_object_ku("ServicePoint__c"), body="obj")
        txn.commit()
        # no index yet
        assert lib.get("agent:index/search") is None

    def test_aliases_present_after_rebuild(self, tmp_path):
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed ServicePoint entity for alias rebuild test")
        txn.add_ku(_sf_object_ku("ServicePoint__c"), body="obj")
        txn.commit()
        rebuild_indexes(lib, "dev", "build index")
        con = retrieve.open_index(lib)
        aliases = _aliases_for(con, "ServicePoint__c")
        assert "service point" in aliases

    def test_aliases_regenerate_on_second_rebuild(self, tmp_path):
        """After adding a new entity, rebuilding regenerates with its aliases."""
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed ServicePoint")
        txn.add_ku(_sf_object_ku("ServicePoint__c"), body="obj")
        txn.commit()
        rebuild_indexes(lib, "dev", "first rebuild")

        # add a new entity
        txn2 = lib.begin("dev", "add MeterPoint")
        txn2.add_ku(_sf_object_ku("MeterPoint__c"), body="obj")
        txn2.commit()
        rebuild_indexes(lib, "dev", "second rebuild after adding MeterPoint")

        con = retrieve.open_index(lib)
        hits = _resolve(con, "meter point")
        assert any(h["name"] == "MeterPoint__c" for h in hits)

    def test_curated_glossary_alias_after_rebuild(self, tmp_path):
        """Glossary alias is present after a rebuild (I13 round-trip)."""
        lib = _lib(tmp_path)
        txn = lib.begin("dev", "seed glossary and entity")
        txn.add_ku(_sf_object_ku("MeterPoint__c"), body="obj")
        gku, gbody = _glossary_ku("meter-glossary", ["MeterPoint__c"],
                                  "punkt poboru\n")
        txn.add_ku(gku, body=gbody)
        txn.commit()
        rebuild_indexes(lib, "dev", "rebuild with glossary")
        con = retrieve.open_index(lib)
        hits = _resolve(con, "punkt poboru")
        assert any(h["name"] == "MeterPoint__c" for h in hits)
        mp_hit = next(h for h in hits if h["name"] == "MeterPoint__c")
        assert mp_hit["via"] == "curated"
