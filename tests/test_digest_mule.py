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
    # the config-only file declares no flows -> not a file KU
    assert {f.rel for f in d.files} == {"acme-orders-api.xml", "orders-impl.xml"}
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
