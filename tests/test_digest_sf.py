"""Salesforce digest — tested on a synthetic force-app mirroring real shapes
(per-field XML, trigger naming its service class, class-to-class calls).
Kept synthetic so the suite is reproducible without any org export.
"""
from librarian import Librarian, Store
from librarian.digest import salesforce as sf


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
    # objects
    fo = base / "objects" / "Formulation__c"
    _write(fo / "Formulation__c.object-meta.xml", OBJECT_META.format(label="Formulation"))
    _write(fo / "fields" / "FormulationRequest__c.field-meta.xml",
           LOOKUP_FIELD.format(name="FormulationRequest__c", label="Formulation Request",
                               ref="FormulationRequest__c"))
    _write(fo / "fields" / "TotalCost__c.field-meta.xml",
           PLAIN_FIELD.format(name="TotalCost__c", label="Total Cost", type="Currency"))
    fr = base / "objects" / "FormulationRequest__c"
    _write(fr / "FormulationRequest__c.object-meta.xml", OBJECT_META.format(label="Formulation Request"))
    # trigger -> service class
    _write(base / "triggers" / "FormulationTrigger.trigger",
           "trigger FormulationTrigger on Formulation__c (after insert) {\n"
           "    FormulationTriggerService.executeAfterInsert(Trigger.newMap);\n}\n")
    # service class -> Helper, references both objects
    _write(base / "classes" / "FormulationTriggerService.cls",
           "public with sharing class FormulationTriggerService {\n"
           "    public static void executeAfterInsert(Map<Id, Formulation__c> m) {\n"
           "        Helper.touch();\n"
           "        for (FormulationRequest__c r : [SELECT Id FROM FormulationRequest__c]) {}\n"
           "    }\n}\n")
    _write(base / "classes" / "Helper.cls",
           "public class Helper {\n    public static void touch() {}\n}\n")
    # a flow touching an object + calling apex
    _write(base / "flows" / "AssignFlow.flow-meta.xml",
           '<?xml version="1.0" encoding="UTF-8"?>\n'
           '<Flow xmlns="http://soap.sforce.com/2006/04/metadata">\n'
           '    <processType>AutoLaunchedFlow</processType>\n'
           '    <recordUpdates><name>u</name><object>FormulationRequest__c</object></recordUpdates>\n'
           '    <actionCalls><name>a</name><actionType>apex</actionType><actionName>Helper</actionName></actionCalls>\n'
           '    <start><object>FormulationRequest__c</object></start>\n'
           '</Flow>\n')
    # an LWC importing apex + composing a child component
    _write(base / "lwc" / "createFormulation" / "createFormulation.js",
           "import { LightningElement } from 'lwc';\n"
           "import touch from '@salesforce/apex/Helper.touch';\n"
           "import child from 'c/childCmp';\n"
           "export default class CreateFormulation extends LightningElement {}\n")
    _write(base / "lwc" / "childCmp" / "childCmp.js",
           "import { LightningElement } from 'lwc';\n"
           "export default class ChildCmp extends LightningElement {}\n")
    # flexipage for a custom object, embedding a custom LWC
    _write(base / "flexipages" / "Formulation_Record.flexipage-meta.xml",
           '<?xml version="1.0" encoding="UTF-8"?>\n'
           '<FlexiPage xmlns="http://soap.sforce.com/2006/04/metadata">\n'
           '    <sobjectType>Formulation__c</sobjectType>\n'
           '    <flexiPageRegions><itemInstances><componentInstance>\n'
           '        <componentName>c:createFormulation</componentName>\n'
           '    </componentInstance></itemInstances></flexiPageRegions>\n'
           '</FlexiPage>\n')
    # permission set granting a custom object, a STANDARD object, and an Apex class
    _write(base / "permissionsets" / "PS_Form.permissionset-meta.xml",
           '<?xml version="1.0" encoding="UTF-8"?>\n'
           '<PermissionSet xmlns="http://soap.sforce.com/2006/04/metadata">\n'
           '    <label>PS Form</label>\n'
           '    <objectPermissions><object>Formulation__c</object><allowRead>true</allowRead></objectPermissions>\n'
           '    <objectPermissions><object>Account</object><allowRead>true</allowRead></objectPermissions>\n'
           '    <fieldPermissions><field>Formulation__c.TotalCost__c</field><readable>true</readable></fieldPermissions>\n'
           '    <classAccesses><apexClass>FormulationTriggerService</apexClass><enabled>true</enabled></classAccesses>\n'
           '</PermissionSet>\n')
    # profile granting the same custom object + a class
    _write(base / "profiles" / "TestProfile.profile-meta.xml",
           '<?xml version="1.0" encoding="UTF-8"?>\n'
           '<Profile xmlns="http://soap.sforce.com/2006/04/metadata">\n'
           '    <objectPermissions><object>Formulation__c</object><allowRead>true</allowRead></objectPermissions>\n'
           '    <classAccesses><apexClass>Helper</apexClass><enabled>true</enabled></classAccesses>\n'
           '</Profile>\n')
    # permission set group bundling the permission set
    _write(base / "permissionsetgroups" / "PSG_Form.permissionsetgroup-meta.xml",
           '<?xml version="1.0" encoding="UTF-8"?>\n'
           '<PermissionSetGroup xmlns="http://soap.sforce.com/2006/04/metadata">\n'
           '    <label>PSG Form</label>\n'
           '    <permissionSets>PS_Form</permissionSets>\n'
           '</PermissionSetGroup>\n')
    return root / "force-app"


def test_parse_produces_expected_shapes(tmp_path):
    fa = make_force_app(tmp_path)
    d = sf.parse_salesforce(fa)
    assert {o.name for o in d.objects} == {"Formulation__c", "FormulationRequest__c"}
    assert {c.name for c in d.classes} == {"FormulationTriggerService", "Helper"}
    assert [t.name for t in d.triggers] == ["FormulationTrigger"]
    trig = d.triggers[0]
    assert trig.sobject == "Formulation__c"
    svc = next(c for c in d.classes if c.name == "FormulationTriggerService")
    assert "Helper" in svc.class_refs
    assert {"Formulation__c", "FormulationRequest__c"} <= svc.sobject_refs


def test_ingest_creates_kus_and_graph(tmp_path):
    fa = make_force_app(tmp_path)
    lib = Librarian(Store(tmp_path / "mem"))
    rep, d = sf.ingest_salesforce(lib, fa, "dev", "ingest sample SF metadata")
    assert rep.ok
    for ku_id in ("salesforce:object/Formulation__c", "salesforce:apexclass/FormulationTriggerService",
                  "salesforce:trigger/FormulationTrigger", "salesforce:graph/sf"):
        assert lib.get(ku_id) is not None
    # graph KU is structured tier
    assert lib.get("salesforce:graph/sf").tier == "structured"


def test_graph_queries(tmp_path):
    fa = make_force_app(tmp_path)
    lib = Librarian(Store(tmp_path / "mem"))
    sf.ingest_salesforce(lib, fa, "dev", "ingest sample SF metadata")
    g = sf.load_graph(lib)

    assert sf.triggers_on(g, "Formulation__c") == ["FormulationTrigger"]
    field_names = {f["name"] for f in sf.fields_of(g, "Formulation__c")}
    assert {"FormulationRequest__c", "TotalCost__c"} <= field_names
    assert "trigger/FormulationTrigger" in sf.who_calls(g, "FormulationTriggerService")
    assert "apexclass/FormulationTriggerService" in sf.who_calls(g, "Helper")
    assert "Helper" in sf.calls_of(g, "FormulationTriggerService")
    # lookup edge present
    assert any(e["type"] == "lookup" and e["dst"] == "object/FormulationRequest__c"
               for e in g["edges"])


def test_flows_and_lwc(tmp_path):
    fa = make_force_app(tmp_path)
    d = sf.parse_salesforce(fa)
    flow = next(f for f in d.flows if f.name == "AssignFlow")
    assert "FormulationRequest__c" in flow.objects
    assert flow.class_refs == {"Helper"}
    cmp = next(l for l in d.lwc if l.name == "createFormulation")
    assert cmp.class_refs == {"Helper"} and cmp.lwc_refs == {"childCmp"}

    lib = Librarian(Store(tmp_path / "mem"))
    sf.ingest_salesforce(lib, fa, "dev", "ingest SF incl flows and LWC")
    assert lib.get("salesforce:flow/AssignFlow") is not None
    assert lib.get("salesforce:lwc/createFormulation") is not None

    g = sf.load_graph(lib)
    assert "AssignFlow" in sf.flows_touching(g, "FormulationRequest__c")
    callers = sf.who_calls(g, "Helper")
    assert "flow/AssignFlow" in callers and "lwc/createFormulation" in callers
    assert "lwc/createFormulation" in sf.components_using(g, "Helper")
    assert any(e["type"] == "uses-component" and e["src"] == "lwc/createFormulation"
               and e["dst"] == "lwc/childCmp" for e in g["edges"])


def test_flexipage_permset_profile_psg(tmp_path):
    fa = make_force_app(tmp_path)
    d = sf.parse_salesforce(fa)
    fp = next(f for f in d.flexipages if f.name == "Formulation_Record")
    assert fp.sobject == "Formulation__c" and fp.lwc_refs == {"createFormulation"}
    ps = next(a for a in d.accesses if a.name == "PS_Form")
    assert ps.kind == "permissionset"
    assert {"Formulation__c", "Account"} <= ps.objects
    assert "FormulationTriggerService" in ps.classes
    assert any(a.kind == "profile" and a.name == "TestProfile" for a in d.accesses)
    psg = next(p for p in d.permsetgroups if p.name == "PSG_Form")
    assert psg.permsets == {"PS_Form"}

    lib = Librarian(Store(tmp_path / "mem"))
    sf.ingest_salesforce(lib, fa, "dev", "ingest SF metadata incl access and pages")
    for kid in ("salesforce:flexipage/Formulation_Record", "salesforce:permissionset/PS_Form",
                "salesforce:profile/TestProfile", "salesforce:permsetgroup/PSG_Form"):
        assert lib.get(kid) is not None

    g = sf.load_graph(lib)
    assert "flexipage/Formulation_Record" in sf.pages_for(g, "Formulation__c")
    assert "permissionset/PS_Form" in sf.grants_on(g, "Formulation__c")
    assert "profile/TestProfile" in sf.grants_on(g, "Formulation__c")
    # a STANDARD object referenced by a permset becomes an external node + grant edge
    assert "permissionset/PS_Form" in sf.grants_on(g, "Account")
    assert any(n.get("external") for n in g["nodes"] if n["id"] == "object/Account")
    # PSG contains the permset; flexipage embeds the LWC
    assert "permissionset/PS_Form" in sf.neighbors(g, "permsetgroup/PSG_Form", "out", "contains")
    assert "lwc/createFormulation" in sf.neighbors(g, "flexipage/Formulation_Record", "out", "embeds")


def test_reingest_unchanged_is_noop(tmp_path):
    fa = make_force_app(tmp_path)
    lib = Librarian(Store(tmp_path / "mem"))
    sf.ingest_salesforce(lib, fa, "dev", "ingest sample SF metadata")
    gen = lib.manifest.generation
    rep, _ = sf.ingest_salesforce(lib, fa, "dev", "re-ingest identical SF metadata")
    assert rep.unchanged and lib.manifest.generation == gen
