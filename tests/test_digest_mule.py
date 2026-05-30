"""Mule digest — synthetic fixtures (cross-file flow-refs, connectors).
Reproducible without any real Mule repo; validate against a real app later.
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
