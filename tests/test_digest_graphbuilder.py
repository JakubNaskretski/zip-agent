"""Salesforce digest (graph-builder-backed adapter) — tested on a synthetic
force-app mirroring real shapes (per-field XML, a trigger naming its service
class, class-to-class calls, a flow, an LWC, a flexipage, perm sets/profile/PSG).

Kept synthetic so the suite is reproducible without any org export. These mirror
the behavioral expectations the retired ``digest/salesforce.py`` encoded; the
engine resolves Apex calls at method granularity, so a few helper checks are
membership (not equality) — exactly how the old graph-query tests were written.
"""
from librarian import Librarian, Store
from librarian.digest import graphbuilder as sf


OBJECT_META = """<?xml version="1.0" encoding="UTF-8"?>
<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>{label}</label>
</CustomObject>"""

LOOKUP_FIELD = """<?xml version="1.0" encoding="UTF-8"?>
<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">
    <fullName>{name}</fullName>
    <label>{label}</label>
    <referenceTo>{ref}</referenceTo>
    <type>Lookup</type>
</CustomField>"""

PLAIN_FIELD = """<?xml version="1.0" encoding="UTF-8"?>
<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">
    <fullName>{name}</fullName>
    <label>{label}</label>
    <type>{type}</type>
</CustomField>"""


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def make_force_app(root):
    base = root / "force-app" / "main" / "default"
    fo = base / "objects" / "MeterPoint__c"
    _write(fo / "MeterPoint__c.object-meta.xml", OBJECT_META.format(label="MeterPoint"))
    _write(fo / "fields" / "MeterPointRequest__c.field-meta.xml",
           LOOKUP_FIELD.format(name="MeterPointRequest__c", label="MeterPoint Request",
                               ref="MeterPointRequest__c"))
    _write(fo / "fields" / "TotalCost__c.field-meta.xml",
           PLAIN_FIELD.format(name="TotalCost__c", label="Total Cost", type="Currency"))
    fr = base / "objects" / "MeterPointRequest__c"
    _write(fr / "MeterPointRequest__c.object-meta.xml", OBJECT_META.format(label="MeterPoint Request"))
    _write(base / "triggers" / "MeterPointTrigger.trigger",
           "trigger MeterPointTrigger on MeterPoint__c (after insert) {\n"
           "    MeterPointTriggerService.executeAfterInsert(Trigger.newMap);\n}\n")
    _write(base / "classes" / "MeterPointTriggerService.cls",
           "public with sharing class MeterPointTriggerService {\n"
           "    public static void executeAfterInsert(Map<Id, MeterPoint__c> m) {\n"
           "        Helper.touch();\n"
           "        for (MeterPointRequest__c r : [SELECT Id FROM MeterPointRequest__c]) {}\n"
           "    }\n}\n")
    _write(base / "classes" / "Helper.cls",
           "public class Helper {\n    public static void touch() {}\n}\n")
    _write(base / "flows" / "AssignFlow.flow-meta.xml",
           '<?xml version="1.0" encoding="UTF-8"?>\n'
           '<Flow xmlns="http://soap.sforce.com/2006/04/metadata">\n'
           '    <processType>AutoLaunchedFlow</processType>\n'
           '    <recordUpdates><name>u</name><object>MeterPointRequest__c</object></recordUpdates>\n'
           '    <actionCalls><name>a</name><actionType>apex</actionType><actionName>Helper</actionName></actionCalls>\n'
           '    <start><object>MeterPointRequest__c</object></start>\n'
           '</Flow>\n')
    _write(base / "lwc" / "createMeterPoint" / "createMeterPoint.js",
           "import { LightningElement } from 'lwc';\n"
           "import touch from '@salesforce/apex/Helper.touch';\n"
           "import child from 'c/childCmp';\n"
           "export default class CreateMeterPoint extends LightningElement {}\n")
    _write(base / "lwc" / "childCmp" / "childCmp.js",
           "import { LightningElement } from 'lwc';\n"
           "export default class ChildCmp extends LightningElement {}\n")
    _write(base / "flexipages" / "MeterPoint_Record.flexipage-meta.xml",
           '<?xml version="1.0" encoding="UTF-8"?>\n'
           '<FlexiPage xmlns="http://soap.sforce.com/2006/04/metadata">\n'
           '    <sobjectType>MeterPoint__c</sobjectType>\n'
           '    <flexiPageRegions><itemInstances><componentInstance>\n'
           '        <componentName>c:createMeterPoint</componentName>\n'
           '    </componentInstance></itemInstances></flexiPageRegions>\n'
           '</FlexiPage>\n')
    _write(base / "permissionsets" / "PS_Form.permissionset-meta.xml",
           '<?xml version="1.0" encoding="UTF-8"?>\n'
           '<PermissionSet xmlns="http://soap.sforce.com/2006/04/metadata">\n'
           '    <label>PS Form</label>\n'
           '    <objectPermissions><object>MeterPoint__c</object><allowRead>true</allowRead></objectPermissions>\n'
           '    <objectPermissions><object>Account</object><allowRead>true</allowRead></objectPermissions>\n'
           '    <fieldPermissions><field>MeterPoint__c.TotalCost__c</field><readable>true</readable></fieldPermissions>\n'
           '    <classAccesses><apexClass>MeterPointTriggerService</apexClass><enabled>true</enabled></classAccesses>\n'
           '</PermissionSet>\n')
    _write(base / "profiles" / "TestProfile.profile-meta.xml",
           '<?xml version="1.0" encoding="UTF-8"?>\n'
           '<Profile xmlns="http://soap.sforce.com/2006/04/metadata">\n'
           '    <objectPermissions><object>MeterPoint__c</object><allowRead>true</allowRead></objectPermissions>\n'
           '    <classAccesses><apexClass>Helper</apexClass><enabled>true</enabled></classAccesses>\n'
           '</Profile>\n')
    _write(base / "permissionsetgroups" / "PSG_Form.permissionsetgroup-meta.xml",
           '<?xml version="1.0" encoding="UTF-8"?>\n'
           '<PermissionSetGroup xmlns="http://soap.sforce.com/2006/04/metadata">\n'
           '    <label>PSG Form</label>\n'
           '    <permissionSets>PS_Form</permissionSets>\n'
           '</PermissionSetGroup>\n')
    return root / "force-app"


def test_digest_creates_kus_graph_and_engine_ku(tmp_path):
    fa = make_force_app(tmp_path)
    lib = Librarian(Store(tmp_path / "mem"))
    rep, dg = sf.ingest_salesforce(lib, fa, "dev", "ingest sample SF metadata")
    assert rep.ok
    for ku_id in ("salesforce:object/MeterPoint__c", "salesforce:apexclass/MeterPointTriggerService",
                  "salesforce:trigger/MeterPointTrigger", "salesforce:graph/sf"):
        assert lib.get(ku_id) is not None
    assert lib.get("salesforce:graph/sf").tier == "structured"
    # the vendored engine is registered as a built-in tool KU carrying the pin
    tool = lib.get("agent:tool/graphbuilder")
    assert tool is not None and tool.tier == "built-in" and tool.kind == "tool"
    assert tool.provenance.get("vendored_sha") == sf._VENDORED_SHA
    # build diagnostics are present (clean synthetic input -> no errors)
    assert dg.errors == [] and dg.skipped == []
    assert "node_types" in dg.summary() and dg.summary()["nodes"] > 0


def test_graph_queries(tmp_path):
    fa = make_force_app(tmp_path)
    lib = Librarian(Store(tmp_path / "mem"))
    sf.ingest_salesforce(lib, fa, "dev", "ingest sample SF metadata")
    g = sf.load_graph(lib)

    assert sf.triggers_on(g, "MeterPoint__c") == ["MeterPointTrigger"]
    fields = {f["name"]: f["type"] for f in sf.fields_of(g, "MeterPoint__c")}
    assert {"MeterPointRequest__c", "TotalCost__c"} <= set(fields)
    assert fields["TotalCost__c"] == "Currency"
    # caller resolution (class- and method-granularity both surface the class)
    assert "trigger/MeterPointTrigger" in sf.who_calls(g, "MeterPointTriggerService")
    assert "apexclass/MeterPointTriggerService" in sf.who_calls(g, "Helper")
    assert "Helper" in sf.calls_of(g, "MeterPointTriggerService")
    assert any(e["type"] == "lookup" and e["dst"] == "object/MeterPointRequest__c"
               for e in g["edges"])


def test_flows_and_lwc(tmp_path):
    fa = make_force_app(tmp_path)
    lib = Librarian(Store(tmp_path / "mem"))
    sf.ingest_salesforce(lib, fa, "dev", "ingest SF incl flows and LWC")
    assert lib.get("salesforce:flow/AssignFlow") is not None
    assert lib.get("salesforce:lwc/createMeterPoint") is not None

    g = sf.load_graph(lib)
    assert "AssignFlow" in sf.flows_touching(g, "MeterPointRequest__c")
    callers = sf.who_calls(g, "Helper")
    assert "flow/AssignFlow" in callers and "lwc/createMeterPoint" in callers
    assert "lwc/createMeterPoint" in sf.components_using(g, "Helper")
    assert any(e["type"] == "uses-component" and e["src"] == "lwc/createMeterPoint"
               and e["dst"] == "lwc/childCmp" for e in g["edges"])


def test_flexipage_permset_profile_psg(tmp_path):
    fa = make_force_app(tmp_path)
    lib = Librarian(Store(tmp_path / "mem"))
    sf.ingest_salesforce(lib, fa, "dev", "ingest SF metadata incl access and pages")
    for kid in ("salesforce:flexipage/MeterPoint_Record", "salesforce:permissionset/PS_Form",
                "salesforce:profile/TestProfile", "salesforce:permsetgroup/PSG_Form"):
        assert lib.get(kid) is not None

    g = sf.load_graph(lib)
    assert "flexipage/MeterPoint_Record" in sf.pages_for(g, "MeterPoint__c")
    assert "permissionset/PS_Form" in sf.grants_on(g, "MeterPoint__c")
    assert "profile/TestProfile" in sf.grants_on(g, "MeterPoint__c")
    # a STANDARD object referenced by a permset becomes an external stub + grant edge
    assert "permissionset/PS_Form" in sf.grants_on(g, "Account")
    assert any(n.get("external") for n in g["nodes"] if n["id"] == "object/Account")
    assert "permissionset/PS_Form" in sf.neighbors(g, "permsetgroup/PSG_Form", "out", "contains")
    assert "lwc/createMeterPoint" in sf.neighbors(g, "flexipage/MeterPoint_Record", "out", "embeds")


def test_entities_join_cross_source(tmp_path):
    """Each component's KU carries the object names it deals with, so the entity
    bridge can join it (trigger/flexipage subject object, permset grants)."""
    fa = make_force_app(tmp_path)
    lib = Librarian(Store(tmp_path / "mem"))
    sf.ingest_salesforce(lib, fa, "dev", "ingest sample SF metadata")
    obj = lib.get("salesforce:object/MeterPoint__c")
    assert {"MeterPoint__c", "TotalCost__c", "MeterPointRequest__c"} <= set(obj.entities)
    trig = lib.get("salesforce:trigger/MeterPointTrigger")
    assert "MeterPoint__c" in trig.entities
    ps = lib.get("salesforce:permissionset/PS_Form")
    assert {"MeterPoint__c", "Account"} <= set(ps.entities)


def test_reingest_unchanged_is_noop(tmp_path):
    fa = make_force_app(tmp_path)
    lib = Librarian(Store(tmp_path / "mem"))
    sf.ingest_salesforce(lib, fa, "dev", "ingest sample SF metadata")
    gen = lib.manifest.generation
    rep, _ = sf.ingest_salesforce(lib, fa, "dev", "re-ingest identical SF metadata")
    assert rep.unchanged and lib.manifest.generation == gen
