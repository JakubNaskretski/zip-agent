"""OmniStudio digest — synthetic fixtures that mirror the REAL standard format
(verified against a live trial org's Designer-built components):

  - OmniScript/IP: <type>+<subType> give the canonical name `Type_SubType`;
    element refs live in nested <omniProcessElements>/<propertySetConfig> JSON
    (integrationProcedureKey -> IP, bundle -> Data Mapper, remoteClass -> Apex).
  - Data Mapper: structured <omniDataTransformItem> XML (inputObjectName ->
    SObject); canonical name = <name>; no JSON blob.
  - Versions: multiple files per component; keep the active one.

Content is fictional (Acme*); the trial org's real names never enter the repo.
"""
import json

from librarian import Librarian, Store
from librarian.digest import salesforce as sf
from librarian.digest import omnistudio as om


def test_collect_refs_finds_all_kinds():
    d = {"children": [
        {"propSetMap": {"integrationProcedureKey": "Acme_Fetch"}},
        {"propSetMap": {"bundle": "AcmeMapper"}},
        {"propSetMap": {"remoteClass": "AcmeController"}},
        {"propSetMap": {"lwcName": "acmeWidget"}},
    ]}
    r = om.collect_refs(d)
    assert r["ip"] == {"Acme_Fetch"} and r["datamapper"] == {"AcmeMapper"}
    assert r["apex"] == {"AcmeController"} and r["lwc"] == {"acmeWidget"}


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def _omniscript(active="true", version="2"):
    # OmniScript: type/subType + elements in <omniProcessElements>/<propertySetConfig>
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<OmniScript xmlns="http://soap.sforce.com/2006/04/metadata">
    <name>createThing</name>
    <type>Acme</type>
    <subType>Create</subType>
    <isActive>{active}</isActive>
    <versionNumber>{version}</versionNumber>
    <propertySetConfig>{{"title":"Create"}}</propertySetConfig>
    <omniProcessElements>
        <name>IPAction1</name><type>Integration Procedure Action</type>
        <propertySetConfig>{{"integrationProcedureKey":"Acme_Fetch"}}</propertySetConfig>
    </omniProcessElements>
    <omniProcessElements>
        <name>DRAction1</name><type>DataRaptor Extract Action</type>
        <propertySetConfig>{{"bundle":"AcmeMapper"}}</propertySetConfig>
    </omniProcessElements>
</OmniScript>
"""


def make_omni_app(root):
    base = root / "force-app" / "main" / "default"
    _w(base / "classes" / "AcmeController.cls", "public class AcmeController {}\n")
    # OmniScript v1 (empty, inactive) + v2 (active, real) -> dedup keeps v2
    _w(base / "omniScripts" / "Acme_Create_English_1.os-meta.xml",
       """<?xml version="1.0" encoding="UTF-8"?>
<OmniScript xmlns="http://soap.sforce.com/2006/04/metadata">
    <type>Acme</type><subType>Create</subType><isActive>false</isActive><versionNumber>1</versionNumber>
</OmniScript>
""")
    _w(base / "omniScripts" / "Acme_Create_English_2.os-meta.xml", _omniscript())
    # Integration Procedure referenced as Acme_Fetch (type_subType), calls the Data Mapper
    _w(base / "omniIntegrationProcedures" / "Acme_Fetch_Procedure_1.oip-meta.xml",
       """<?xml version="1.0" encoding="UTF-8"?>
<OmniIntegrationProcedure xmlns="http://soap.sforce.com/2006/04/metadata">
    <name>New Integration Procedure</name>
    <type>Acme</type><subType>Fetch</subType><isActive>true</isActive><versionNumber>1</versionNumber>
    <omniProcessElements>
        <name>DR1</name><type>DataRaptor Extract Action</type>
        <propertySetConfig>{"bundle":"AcmeMapper"}</propertySetConfig>
    </omniProcessElements>
</OmniIntegrationProcedure>
""")
    # Data Mapper: structured XML, no propertySetConfig; canonical name = <name>
    _w(base / "omniDataTransforms" / "AcmeMapper_1.rpt-meta.xml",
       """<?xml version="1.0" encoding="UTF-8"?>
<OmniDataTransform xmlns="http://soap.sforce.com/2006/04/metadata">
    <name>AcmeMapper</name><type>Extract</type><isActive>true</isActive><versionNumber>1</versionNumber>
    <omniDataTransformItem>
        <inputObjectName>Account</inputObjectName>
        <outputObjectName>json</outputObjectName>
    </omniDataTransformItem>
</OmniDataTransform>
""")
    return root / "force-app"


def test_canonical_names_refs_and_version_dedup(tmp_path):
    comps = {c.name: c for c in om.parse_omnistudio(make_omni_app(tmp_path))}
    # canonical naming + version dedup (only the active v2 OmniScript survives)
    assert comps["Acme_Create"].otype == "omniscript" and comps["Acme_Create"].version == 2.0
    assert comps["Acme_Fetch"].otype == "integrationprocedure"
    assert comps["AcmeMapper"].otype == "datamapper"
    # references via the real key paths
    assert comps["Acme_Create"].ip_refs == {"Acme_Fetch"}
    assert comps["Acme_Create"].dm_refs == {"AcmeMapper"}
    assert comps["Acme_Fetch"].dm_refs == {"AcmeMapper"}
    # Data Mapper object from structured XML; "json" output format filtered out
    assert comps["AcmeMapper"].object_refs == {"Account"}


def test_omnistudio_dependency_chain_in_graph(tmp_path):
    lib = Librarian(Store(tmp_path / "mem"))
    sf.ingest_salesforce(lib, make_omni_app(tmp_path), "dev", "ingest omnistudio sample")
    g = sf.load_graph(lib)
    assert "integrationprocedure/Acme_Fetch" in sf.neighbors(g, "omniscript/Acme_Create", "out", "calls")
    assert "datamapper/AcmeMapper" in sf.neighbors(g, "omniscript/Acme_Create", "out", "uses")
    assert "datamapper/AcmeMapper" in sf.neighbors(g, "integrationprocedure/Acme_Fetch", "out", "uses")
    assert "object/Account" in sf.neighbors(g, "datamapper/AcmeMapper", "out", "maps")


def test_datasource_config_reference(tmp_path):
    """FlexCard binds its data source via <dataSourceConfig>.ipMethod."""
    base = tmp_path / "force-app" / "main" / "default"
    _w(base / "omniUiCard" / "AcctCard.ouc-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<OmniUiCard xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '    <name>AcctCard</name>\n    <propertySetConfig>{}</propertySetConfig>\n'
       '    <dataSourceConfig>'
       + json.dumps({"dataSource": {"type": "IntegrationProcedure",
                                     "value": {"ipMethod": "Acme_Fetch"}}})
       + '</dataSourceConfig>\n</OmniUiCard>\n')
    card = next(o for o in om.parse_omnistudio(tmp_path / "force-app") if o.name == "AcctCard")
    assert card.otype == "flexcard" and "Acme_Fetch" in card.ip_refs


def test_vlocity_datapack_model(tmp_path):
    base = tmp_path / "force-app" / "main" / "default"
    _w(base / "vlocity" / "DataRaptor" / "DM_Old_DataPack.json", json.dumps({
        "name": "DM_Old", "type": "DataRaptor Extract", "interfaceObjectName": "Dummy__c",
    }))
    dm = next((o for o in om.parse_omnistudio(tmp_path / "force-app") if o.name == "DM_Old"), None)
    assert dm and dm.model == "vlocity" and dm.otype == "datamapper" and "Dummy__c" in dm.object_refs
