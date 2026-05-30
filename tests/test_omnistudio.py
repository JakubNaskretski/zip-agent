"""OmniStudio digest — standard metadata format (XML-meta + embedded JSON) and
Vlocity DataPacks.

The file format and field layout are confirmed against a real trial org's
metadata describe + a real FlexCard export. Component *content* here is synthetic
with fictional names (the trial org has no scripts/IPs/Data Mappers to copy).
"""
import json

from librarian import Librarian, Store
from librarian.digest import salesforce as sf
from librarian.digest import omnistudio as om


def test_collect_refs_finds_all_kinds():
    definition = {"children": [
        {"propSetMap": {"integrationProcedureKey": "Account_Create"}},
        {"propSetMap": {"bundle": "DM_GetAccount"}},
        {"propSetMap": {"remoteClass": "AccountController"}},
        {"propSetMap": {"lwcName": "myWidget"}},
        {"propSetMap": {"objectName": "Account"}},
    ]}
    refs = om.collect_refs(definition)
    assert refs["ip"] == {"Account_Create"}
    assert refs["datamapper"] == {"DM_GetAccount"}
    assert refs["apex"] == {"AccountController"}
    assert refs["lwc"] == {"myWidget"}
    assert refs["object"] == {"Account"}


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def _meta(root_tag, name, ps_config):
    """A standard OmniStudio *-meta.xml with the definition embedded as JSON."""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<{root_tag} xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            f'    <name>{name}</name>\n'
            f'    <propertySetConfig>{json.dumps(ps_config)}</propertySetConfig>\n'
            f'</{root_tag}>\n')


def make_omni_force_app(root):
    base = root / "force-app" / "main" / "default"
    # apex + lwc so the OmniScript's apex/lwc refs resolve to real KUs
    _write(base / "classes" / "AccountController.cls",
           "public with sharing class AccountController {}\n")
    _write(base / "lwc" / "myWidget" / "myWidget.js",
           "import { LightningElement } from 'lwc';\nexport default class MyWidget extends LightningElement {}\n")
    # OmniScript referencing an IP, a Data Mapper, apex, lwc, object (real *.os-meta.xml format)
    _write(base / "omniScripts" / "CreateAccount.os-meta.xml",
           _meta("OmniScript", "CreateAccount", {"children": [
               {"propSetMap": {"integrationProcedureKey": "Account_Create"}},
               {"propSetMap": {"bundle": "DM_GetAccount"}},
               {"propSetMap": {"remoteClass": "AccountController"}},
               {"propSetMap": {"lwcName": "myWidget"}},
               {"propSetMap": {"objectName": "Account"}},
           ]}))
    # the Integration Procedure it calls
    _write(base / "omniIntegrationProcedures" / "Account_Create.oip-meta.xml",
           _meta("OmniIntegrationProcedure", "Account_Create", {"children": []}))
    # the Data Mapper, mapping to Account
    _write(base / "omniDataTransforms" / "DM_GetAccount.rpt-meta.xml",
           _meta("OmniDataTransform", "DM_GetAccount", {"interfaceObjectName": "Account"}))
    return root / "force-app"


def test_standard_omnistudio_parsed_and_graphed(tmp_path):
    fa = make_omni_force_app(tmp_path)
    d = sf.parse_salesforce(fa)
    by_type = {(o.otype, o.name) for o in d.omni}
    assert ("omniscript", "CreateAccount") in by_type
    assert ("integrationprocedure", "Account_Create") in by_type
    assert ("datamapper", "DM_GetAccount") in by_type
    assert all(o.model == "standard" for o in d.omni)

    os_c = next(o for o in d.omni if o.name == "CreateAccount")
    assert os_c.ip_refs == {"Account_Create"} and os_c.dm_refs == {"DM_GetAccount"}
    assert os_c.apex_refs == {"AccountController"} and "Account" in os_c.object_refs

    lib = Librarian(Store(tmp_path / "mem"))
    sf.ingest_salesforce(lib, fa, "dev", "ingest OmniStudio standard sample")
    for kid in ("salesforce:omniscript/CreateAccount",
                "salesforce:integrationprocedure/Account_Create",
                "salesforce:datamapper/DM_GetAccount"):
        assert lib.get(kid) is not None

    g = sf.load_graph(lib)
    os_id = "omniscript/CreateAccount"
    assert "integrationprocedure/Account_Create" in sf.neighbors(g, os_id, "out", "calls")
    assert "datamapper/DM_GetAccount" in sf.neighbors(g, os_id, "out", "uses")
    assert "apexclass/AccountController" in sf.neighbors(g, os_id, "out", "calls")
    assert "lwc/myWidget" in sf.neighbors(g, os_id, "out", "embeds")
    assert "object/Account" in sf.neighbors(g, os_id, "out", "touches")
    assert "object/Account" in sf.neighbors(g, "datamapper/DM_GetAccount", "out", "maps")


def test_datasource_config_reference(tmp_path):
    """A FlexCard/OmniScript binds its data source via <dataSourceConfig> — the
    IP it points at (ipMethod) must be picked up too."""
    base = tmp_path / "force-app" / "main" / "default"
    card = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<OmniUiCard xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '    <name>AcctCard</name>\n'
            '    <propertySetConfig>{}</propertySetConfig>\n'
            '    <dataSourceConfig>'
            + json.dumps({"dataSource": {"type": "IntegrationProcedure",
                                         "value": {"ipMethod": "Account_Create"}}})
            + '</dataSourceConfig>\n</OmniUiCard>\n')
    _write(base / "omniUiCard" / "AcctCard.ouc-meta.xml", card)
    # parse OmniStudio directly (raw extraction, before ref-resolution constrains to known)
    comps = om.parse_omnistudio(tmp_path / "force-app")
    card_c = next(o for o in comps if o.name == "AcctCard")
    assert card_c.otype == "flexcard"
    assert "Account_Create" in card_c.ip_refs       # pulled from dataSourceConfig.ipMethod


def test_vlocity_datapack_model(tmp_path):
    base = tmp_path / "force-app" / "main" / "default"
    _write(base / "objects" / "Dummy__c" / "Dummy__c.object-meta.xml",
           '<?xml version="1.0" encoding="UTF-8"?>\n'
           '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata"><label>D</label></CustomObject>')
    _write(base / "vlocity" / "DataRaptor" / "DM_Old_DataPack.json", json.dumps({
        "name": "DM_Old", "VlocityRecordSObjectType": "DataRaptor",
        "interfaceObjectName": "Dummy__c", "type": "DataRaptor Extract",
    }))
    d = sf.parse_salesforce(tmp_path / "force-app")
    dm = next((o for o in d.omni if o.name == "DM_Old"), None)
    assert dm is not None and dm.model == "vlocity" and dm.otype == "datamapper"
    assert "Dummy__c" in dm.object_refs
