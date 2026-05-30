"""OmniStudio digest (PROVISIONAL) — Integration Procedures, OmniScripts,
Data Mappers, both the standard runtime and Vlocity DataPacks.

Synthetic fixtures only (no OmniStudio sample is available in this repo). These
lock in the extraction *behaviour*; the reference-key names will be tuned against
a real sanitized export.
"""
import json

from librarian import Librarian, Store
from librarian.digest import salesforce as sf
from librarian.digest import omnistudio as om


def test_collect_refs_finds_all_kinds():
    definition = {
        "omniProcessType": "OmniScript",
        "elements": [
            {"propSetMap": {"integrationProcedureKey": "Account_Create"}},
            {"propSetMap": {"bundle": "DM_GetAccount"}},
            {"propSetMap": {"remoteClass": "AccountController"}},
            {"propSetMap": {"lwcName": "myWidget"}},
            {"propSetMap": {"objectName": "Account"}},
        ],
    }
    refs = om.collect_refs(definition)
    assert refs["ip"] == {"Account_Create"}
    assert refs["datamapper"] == {"DM_GetAccount"}
    assert refs["apex"] == {"AccountController"}
    assert refs["lwc"] == {"myWidget"}
    assert refs["object"] == {"Account"}


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def make_omni_force_app(root):
    base = root / "force-app" / "main" / "default"
    # apex + lwc so OmniScript apex/lwc refs resolve to real KUs
    _write(base / "classes" / "AccountController.cls",
           "public with sharing class AccountController {}\n")
    _write(base / "lwc" / "myWidget" / "myWidget.js",
           "import { LightningElement } from 'lwc';\nexport default class MyWidget extends LightningElement {}\n")
    # standard-runtime OmniScript referencing IP, DataMapper, apex, lwc, object
    _write(base / "omniProcesses" / "OS_CreateAccount.json", json.dumps({
        "name": "OS_CreateAccount", "omniProcessType": "OmniScript",
        "elements": [
            {"propSetMap": {"integrationProcedureKey": "Account_Create"}},
            {"propSetMap": {"bundle": "DM_GetAccount"}},
            {"propSetMap": {"remoteClass": "AccountController"}},
            {"propSetMap": {"lwcName": "myWidget"}},
            {"propSetMap": {"objectName": "Account"}},
        ],
    }))
    # the Integration Procedure it calls
    _write(base / "omniProcesses" / "Account_Create.json", json.dumps({
        "name": "Account_Create", "omniProcessType": "Integration Procedure",
        "elements": [{"propSetMap": {"bundle": "DM_GetAccount"}}],
    }))
    # the Data Mapper, mapping to Account
    _write(base / "omniDataTransforms" / "DM_GetAccount.json", json.dumps({
        "name": "DM_GetAccount", "interfaceObjectName": "Account",
    }))
    return root / "force-app"


def test_standard_omnistudio_parsed_and_graphed(tmp_path):
    fa = make_omni_force_app(tmp_path)
    d = sf.parse_salesforce(fa)
    by_type = {(o.otype, o.name) for o in d.omni}
    assert ("omniscript", "OS_CreateAccount") in by_type
    assert ("integrationprocedure", "Account_Create") in by_type
    assert ("datamapper", "DM_GetAccount") in by_type
    assert all(o.model == "standard" for o in d.omni)

    lib = Librarian(Store(tmp_path / "mem"))
    sf.ingest_salesforce(lib, fa, "dev", "ingest OmniStudio standard sample")
    for kid in ("salesforce:omniscript/OS_CreateAccount",
                "salesforce:integrationprocedure/Account_Create",
                "salesforce:datamapper/DM_GetAccount"):
        assert lib.get(kid) is not None

    g = sf.load_graph(lib)
    os_id = "omniscript/OS_CreateAccount"
    assert "integrationprocedure/Account_Create" in sf.neighbors(g, os_id, "out", "calls")
    assert "datamapper/DM_GetAccount" in sf.neighbors(g, os_id, "out", "uses")
    assert "apexclass/AccountController" in sf.neighbors(g, os_id, "out", "calls")
    assert "lwc/myWidget" in sf.neighbors(g, os_id, "out", "embeds")
    assert "object/Account" in sf.neighbors(g, os_id, "out", "touches")
    # DataMapper maps to Account (external object node)
    assert "object/Account" in sf.neighbors(g, "datamapper/DM_GetAccount", "out", "maps")


def test_vlocity_datapack_model(tmp_path):
    base = tmp_path / "force-app" / "main" / "default"
    _write(base / "objects" / "Dummy__c" / "Dummy__c.object-meta.xml",
           '<?xml version="1.0" encoding="UTF-8"?>\n'
           '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata"><label>D</label></CustomObject>')
    _write(base / "vlocity" / "DataRaptor" / "DM_Old_DataPack.json", json.dumps({
        "name": "DM_Old", "VlocityRecordSourceKey": "DataRaptor/DM_Old",
        "interfaceObjectName": "Dummy__c", "type": "DataRaptor Extract",
    }))
    d = sf.parse_salesforce(tmp_path / "force-app")
    dm = next((o for o in d.omni if o.name == "DM_Old"), None)
    assert dm is not None and dm.model == "vlocity" and dm.otype == "datamapper"
    assert "Dummy__c" in dm.object_refs
