"""Mule digest (graph-builder-backed adapter) — synthetic fixtures.

Two fixtures: a minimal cross-file flow-ref/connector pair, and a realistic
APIkit-style app (router file -> impl file + a config-only file) grounded in the
standard Mule 4 layout. Reproducible without any real Mule repo; validate against
a real app later. Fictional Acme data only.
"""
from librarian import Librarian, Store, rebuild_indexes, retrieve
from librarian.digest import mule


ORDERS = """<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:http="http://www.mulesoft.org/schema/mule/http"
      xmlns:db="http://www.mulesoft.org/schema/mule/db">
  <flow name="ordersFlow">
    <http:listener path="/orders"/>
    <flow-ref name="validateSub"/>
    <db:insert/>
    <flow-ref name="syncToSf"/>
  </flow>
</mule>"""

SHARED = """<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:ee="http://www.mulesoft.org/schema/mule/ee/core"
      xmlns:salesforce="http://www.mulesoft.org/schema/mule/salesforce">
  <sub-flow name="validateSub">
    <ee:transform/>
  </sub-flow>
  <flow name="syncToSf">
    <salesforce:create type="Account"/>
  </flow>
</mule>"""


def make_mule_app(root):
    base = root / "src" / "main" / "mule"
    base.mkdir(parents=True)
    (base / "orders.xml").write_text(ORDERS, "utf-8")
    (base / "shared.xml").write_text(SHARED, "utf-8")
    (root / "pom.xml").write_text("<project><name>x</name></project>", "utf-8")  # ignored
    return root


def test_parse_flows_refs_connectors(tmp_path):
    d = mule.parse_mule(make_mule_app(tmp_path))
    flows = {f.name: f for f in d.flows}
    assert set(flows) == {"ordersFlow", "validateSub", "syncToSf"}
    assert flows["validateSub"].kind == "sub-flow"
    assert flows["ordersFlow"].refs == {"validateSub", "syncToSf"}
    assert {"http", "db"} <= flows["ordersFlow"].connectors
    assert "salesforce" in flows["syncToSf"].connectors
    assert "ee" in flows["validateSub"].connectors
    # pom.xml is not a Mule config → not a file KU
    assert {f.rel for f in d.files} == {"orders.xml", "shared.xml"}


def test_ingest_and_graph_queries(tmp_path):
    lib = Librarian(Store(tmp_path / "mem"))
    rep, d = mule.ingest_mule(lib, make_mule_app(tmp_path), "dev", "ingest sample Mule app")
    assert rep.ok
    assert lib.get("mule:orders.xml") is not None
    assert lib.get("mule:graph/mule").tier == "structured"

    g = mule.load_graph(lib)
    assert mule.who_calls(g, "validateSub") == ["ordersFlow"]
    assert set(mule.calls_from(g, "ordersFlow")) == {"validateSub", "syncToSf"}
    assert {"http", "db"} <= set(mule.connectors_used(g, "ordersFlow"))
    assert mule.flows_using(g, "salesforce") == ["syncToSf"]
    assert mule.search_flows(g, "sync") == ["syncToSf"]


def test_cross_file_links_and_entity_bridge(tmp_path):
    lib = Librarian(Store(tmp_path / "mem"))
    mule.ingest_mule(lib, make_mule_app(tmp_path), "dev", "ingest sample Mule app")
    # orders.xml flow-refs flows defined in shared.xml -> file-level reference link
    links = {l["to"] for l in lib.get("mule:orders.xml").links}
    assert "mule:shared.xml" in links

    # flow names join the cross-source entity bridge
    rebuild_indexes(lib, "dev", "build index")
    con = retrieve.open_index(lib)
    hits = {h["ku_id"] for h in retrieve.find_entity(con, "ordersFlow")}
    assert "mule:orders.xml" in hits


# --- realistic APIkit-style app (standard src/main/mule layout) -------------- #
# An API router file flow-refs implementation flows in a second file; a third file
# holds only global configs (no flows). Real APIkit flow names use backslashes
# (e.g. "get:\\orders:cfg") — kept plain here; the authentic form lives in the
# samples/mule app. Fictional Acme data only.
API = """<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:http="http://www.mulesoft.org/schema/mule/http"
      xmlns:apikit="http://www.mulesoft.org/schema/mule/mule-apikit">
  <flow name="acme-orders-api-main">
    <http:listener config-ref="httpListenerConfig" path="/api/*"/>
    <apikit:router config-ref="acme-orders-config"/>
  </flow>
  <flow name="get-orders">
    <flow-ref name="listOrders"/>
  </flow>
  <flow name="post-orders">
    <flow-ref name="createOrder"/>
    <flow-ref name="auditOrder"/>
  </flow>
</mule>"""

IMPL = """<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:db="http://www.mulesoft.org/schema/mule/db"
      xmlns:ee="http://www.mulesoft.org/schema/mule/ee/core">
  <flow name="listOrders">
    <db:select config-ref="dbConfig"><db:sql>SELECT * FROM orders</db:sql></db:select>
    <ee:transform/>
  </flow>
  <flow name="createOrder">
    <flow-ref name="validateOrder"/>
    <db:insert config-ref="dbConfig"><db:sql>INSERT INTO orders</db:sql></db:insert>
  </flow>
  <sub-flow name="validateOrder"><ee:transform/></sub-flow>
</mule>"""

GLOBAL_CFG = """<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:http="http://www.mulesoft.org/schema/mule/http"
      xmlns:db="http://www.mulesoft.org/schema/mule/db">
  <configuration-properties file="config-dev.yaml"/>
  <http:listener-config name="httpListenerConfig"/>
  <db:config name="dbConfig"/>
</mule>"""


def make_apikit_app(root):
    base = root / "src" / "main" / "mule"
    base.mkdir(parents=True)
    (base / "acme-orders-api.xml").write_text(API, "utf-8")
    (base / "orders-impl.xml").write_text(IMPL, "utf-8")
    (base / "global-config.xml").write_text(GLOBAL_CFG, "utf-8")
    return root


def test_apikit_app_graph_and_diagnostics(tmp_path):
    d = mule.parse_mule(make_apikit_app(tmp_path))
    # Phase 3: the config-only file is a file KU too — its global-config /
    # property-load declarations are what you retrieve it for (pre-Phase-3 it
    # was skipped for declaring no flows)
    assert {f.rel for f in d.files} == {"acme-orders-api.xml", "orders-impl.xml",
                                        "global-config.xml"}
    by_rel = {f.rel: f for f in d.files}
    assert by_rel["global-config.xml"].entities == ["dbConfig", "httpListenerConfig"]
    assert d.errors == [] and d.unresolved == []          # well-formed -> clean build
    ids = {n["id"]: n for n in d.graph["nodes"]}
    edges = {(e["src"], e["type"], e["dst"]) for e in d.graph["edges"]}
    # cross-file flow-ref resolves to the real impl flow
    assert ids["muleflow/listOrders"].get("external") is not True
    assert ("muleflow/post-orders", "calls", "muleflow/createOrder") in edges
    # an undefined flow-ref becomes an external stub, not an error
    assert ids["muleflow/auditOrder"].get("external") is True
    # connectors detected across files (incl. the APIkit router namespace)
    conns = {n["label"] for n in d.graph["nodes"] if n["type"] == "muleconnector"}
    assert {"http", "db", "ee", "mule-apikit"} <= conns


def test_apikit_app_ingest_queries(tmp_path):
    lib = Librarian(Store(tmp_path / "mem"))
    mule.ingest_mule(lib, make_apikit_app(tmp_path), "dev", "ingest apikit app")
    g = mule.load_graph(lib)
    assert mule.calls_from(g, "acme-orders-api-main") == []      # router only, no flow-ref
    assert mule.who_calls(g, "validateOrder") == ["createOrder"]
    assert set(mule.calls_from(g, "post-orders")) == {"createOrder", "auditOrder"}
    # the API file references the impl file via its cross-file flow-refs
    links = {l["to"] for l in lib.get("mule:acme-orders-api.xml").links}
    assert "mule:orders-impl.xml" in links


# --- Phase-3 app: authentic APIkit naming + RAML + properties + build files -- #
# Fictional Acme data only. Engine pin 4a59b97 (Phase-3 Mule taxonomy).
P3_API = """<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:http="http://www.mulesoft.org/schema/mule/http"
      xmlns:apikit="http://www.mulesoft.org/schema/mule/mule-apikit">
  <flow name="acme-orders-main">
    <http:listener config-ref="httpListenerConfig" path="/api/*"/>
    <apikit:router config-ref="orders-config"/>
  </flow>
  <flow name="get:\\orders:orders-config">
    <flow-ref name="listOrders"/>
  </flow>
  <flow name="get:\\orders\\(orderId):orders-config">
    <flow-ref name="getOrder"/>
  </flow>
</mule>"""

P3_IMPL = """<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:db="http://www.mulesoft.org/schema/mule/db">
  <flow name="listOrders">
    <db:select config-ref="dbConfig"><db:sql>SELECT 1</db:sql></db:select>
  </flow>
  <flow name="getOrder">
    <db:select config-ref="dbConfig"/>
  </flow>
  <flow name="nightlySync">
    <scheduler>
      <scheduling-strategy><fixed-frequency frequency="60000"/></scheduling-strategy>
    </scheduler>
    <db:select config-ref="dbConfig" target="${batch.target}"/>
  </flow>
</mule>"""

P3_GLOBAL = """<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:http="http://www.mulesoft.org/schema/mule/http"
      xmlns:db="http://www.mulesoft.org/schema/mule/db"
      xmlns:apikit="http://www.mulesoft.org/schema/mule/mule-apikit">
  <configuration-properties file="config-dev.yaml"/>
  <http:listener-config name="httpListenerConfig"/>
  <db:config name="dbConfig">
    <db:my-sql-connection host="${db.host}" password="${secure::db.password}"/>
  </db:config>
  <apikit:config name="orders-config" raml="orders.raml"/>
</mule>"""

P3_RAML = """#%RAML 1.0
title: Acme Orders API
/orders:
  get:
  /{orderId}:
    get:
"""

P3_YAML = "db:\n  host: localhost\nbatch:\n  target: orders\n"

P3_POM = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <artifactId>acme-orders-api</artifactId>
  <dependencies>
    <dependency>
      <groupId>org.mule.connectors</groupId>
      <artifactId>mule-db-connector</artifactId>
    </dependency>
  </dependencies>
</project>"""

P3_DESCRIPTOR = '{"name": "acme-orders-api", "minMuleVersion": "4.4.0", "secureProperties": ["db.password"]}'


def make_phase3_app(root):
    base = root / "src" / "main" / "mule"
    base.mkdir(parents=True)
    (base / "api.xml").write_text(P3_API, "utf-8")
    (base / "impl.xml").write_text(P3_IMPL, "utf-8")
    (base / "global.xml").write_text(P3_GLOBAL, "utf-8")
    res = root / "src" / "main" / "resources"
    (res / "api").mkdir(parents=True)
    (res / "api" / "orders.raml").write_text(P3_RAML, "utf-8")
    (res / "config-dev.yaml").write_text(P3_YAML, "utf-8")
    (root / "pom.xml").write_text(P3_POM, "utf-8")
    (root / "mule-artifact.json").write_text(P3_DESCRIPTOR, "utf-8")
    return root


def test_phase3_support_files_and_kus(tmp_path):
    lib = Librarian(Store(tmp_path / "mem"))
    rep, d = mule.ingest_mule(lib, make_phase3_app(tmp_path), "dev", "ingest phase3 app")
    assert rep.ok
    # config-file KUs unchanged; support files get their own raw KUs
    assert {f.rel for f in d.files} == {"api.xml", "impl.xml", "global.xml"}
    assert {f.rel for f in d.support_files} == {
        "resources/api/orders.raml", "resources/config-dev.yaml",
        "pom.xml", "mule-artifact.json"}
    assert lib.get("mule:resources/api/orders.raml") is not None
    assert lib.get("mule:pom.xml") is not None
    # parsed names land as entities (property keys, resource paths)
    assert "db.host" in lib.get("mule:resources/config-dev.yaml").entities
    assert "/orders" in lib.get("mule:resources/api/orders.raml").entities
    assert d.errors == [] and d.unresolved == []


def test_phase3_queries(tmp_path):
    lib = Librarian(Store(tmp_path / "mem"))
    mule.ingest_mule(lib, make_phase3_app(tmp_path), "dev", "ingest phase3 app")
    g = mule.load_graph(lib)
    assert mule.flow_for_resource(g, "GET", "/orders") == ["get:\\orders:orders-config"]
    assert mule.flow_for_resource(g, "get", "/orders/{orderId}") \
        == ["get:\\orders\\(orderId):orders-config"]
    assert mule.flows_exposed_on(g, "/api/*") == [
        {"flow": "acme-orders-main", "path": "/api/*", "config": "httpListenerConfig"}]
    eps = {e["flow"]: e for e in mule.entrypoints(g)}
    assert eps["acme-orders-main"]["kind"] == "httplistener"
    assert eps["nightlySync"] == {"flow": "nightlySync", "kind": "scheduler",
                                  "detail": "60000"}
    assert mule.flows_reading(g, "batch.target") == ["nightlySync"]
    assert mule.keys_read_by(g, "nightlySync") == ["batch.target"]
    assert {r["path"]: r["methods"] for r in mule.api_resources(g)} == {
        "/orders": ["get"], "/orders/{orderId}": ["get"]}
    assert set(mule.routes_of(g, "orders-config")) == {
        "get:\\orders:orders-config", "get:\\orders\\(orderId):orders-config"}
    assert mule.configs_used(g, "listOrders") == ["dbConfig"]
    assert mule.secure_keys(g) == ["db.password"]
    assert mule.app_dependencies(g) == ["org.mule.connectors:mule-db-connector"]
    # Phase-1 helpers keep working on the same graph (back-compat freeze)
    assert mule.who_calls(g, "listOrders") == ["get:\\orders:orders-config"]
    assert "db" in mule.connectors_used(g, "nightlySync")


def test_phase3_entity_bridge(tmp_path):
    lib = Librarian(Store(tmp_path / "mem"))
    mule.ingest_mule(lib, make_phase3_app(tmp_path), "dev", "ingest phase3 app")
    rebuild_indexes(lib, "dev", "build index")
    con = retrieve.open_index(lib)
    # a property key resolves to the file that defines it
    hits = {h["ku_id"] for h in retrieve.find_entity(con, "db.host")}
    assert "mule:resources/config-dev.yaml" in hits


# --- Phase 5: DataWeave (.dwl) + MUnit (src/test/munit) ---------------------- #
P5_FLOWS = """<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core">
  <flow name="ordersFlow"><logger/></flow>
  <flow name="legacyFlow"><logger/></flow>
</mule>"""

P5_COMMON_DWL = "%dw 2.0\nimport * from dw::core::Strings\nfun normalize(s) = lower(s)\n"
P5_MAPPING_DWL = ("%dw 2.0\noutput application/json\n"
                  "import normalize from modules::Common\n---\n{ id: normalize(payload.id) }\n")

P5_MUNIT = """<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:munit="http://www.mulesoft.org/schema/mule/munit"
      xmlns:munit-tools="http://www.mulesoft.org/schema/mule/munit-tools">
  <munit:test name="ordersFlow-happy">
    <munit:behavior>
      <munit-tools:mock-when processor="db:select"><munit-tools:then-return/></munit-tools:mock-when>
    </munit:behavior>
    <munit:execution><flow-ref name="ordersFlow"/></munit:execution>
  </munit:test>
</mule>"""


def make_phase5_app(root):
    base = root / "src" / "main" / "mule"
    base.mkdir(parents=True)
    (base / "orders.xml").write_text(P5_FLOWS, "utf-8")
    res = root / "src" / "main" / "resources"
    (res / "modules").mkdir(parents=True)
    (res / "modules" / "Common.dwl").write_text(P5_COMMON_DWL, "utf-8")
    (res / "dwl").mkdir(parents=True)
    (res / "dwl" / "mapping.dwl").write_text(P5_MAPPING_DWL, "utf-8")
    munit = root / "src" / "test" / "munit"
    munit.mkdir(parents=True)
    (munit / "orders-test.xml").write_text(P5_MUNIT, "utf-8")
    return root


def test_phase5_dataweave_and_munit(tmp_path):
    lib = Librarian(Store(tmp_path / "mem"))
    app = make_phase5_app(tmp_path)
    rep, d = mule.ingest_mule(lib, app, "dev",
                              "ingest a Mule app with DataWeave and MUnit")
    assert rep.ok
    # .dwl + munit stored as support-file raw KUs (retrievable + FTS), distinct ids
    assert lib.get("mule:resources/modules/Common.dwl") is not None
    assert lib.get("mule:resources/dwl/mapping.dwl") is not None
    assert lib.get("mule:munit/orders-test.xml") is not None

    g = mule.load_graph(lib)
    # MUnit coverage edge resolves to the real flow under test
    assert mule.tests_for(g, "ordersFlow") == ["orders-test.xml#ordersFlow-happy"]
    assert "legacyFlow" in mule.untested_flows(g)        # no test -> coverage gap
    assert "ordersFlow" not in mule.untested_flows(g)
    # DataWeave imports: a LOCAL module resolves to the declaring .dwl rel; a
    # std-library module stays an external module spec
    assert "resources/modules/Common.dwl" in mule.dw_imports(g, "resources/dwl/mapping.dwl")
    assert "dw::core::Strings" in mule.dw_imports(g, "resources/modules/Common.dwl")

    s = d.summary()
    assert s["dataweave"] == 2 and s["munit_tests"] == 1

    # re-ingesting the unchanged app is an I9 no-op (graph merge stays byte-identical)
    gen = lib.manifest.generation
    rep2, _ = mule.ingest_mule(lib, app, "dev", "re-ingest the unchanged phase5 app")
    assert lib.manifest.generation == gen and mule.GRAPH_ID in rep2.unchanged
