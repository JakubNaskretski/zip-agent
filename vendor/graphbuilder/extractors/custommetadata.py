"""Custom Metadata *records* extractor — `*.md-meta.xml`.

Custom Metadata records are the rows of a Custom Metadata Type (the `__mdt`
object, which `objects.py` already emits as an `object` node with
`category="custommetadata"`). Each record ships as one file named
`<Type>.<Record>.md-meta.xml` under `customMetadata/`::

    <CustomMetadata xmlns="http://soap.sforce.com/2006/04/metadata">
      <label>Some Record</label>           <!-- NEVER emitted (display data) -->
      <protected>false</protected>
      <values>
        <field>Rate__c</field>
        <value xsi:type="...">0.42</value>  <!-- NEVER read: this is DATA -->
      </values>
    </CustomMetadata>

Each record becomes a `custommetadatarecord/<Type>.<Record>` node plus a
`references` edge to the `object/<Type>__mdt` it is an instance of (the core
stubs that object if the type definition wasn't retrieved). Only the structural
`<protected>` flag is kept as an attr.

A Custom Metadata record's `<values>` are configuration data: the `<value>` and
the `<field>` names it sets are never read, stored, or emitted — only the
record's name and the link to its type. Parsing is namespace-agnostic and skips
malformed files.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..xmlutil import child_text as _child_text

_SUFFIX = ".md-meta.xml"


class CustomMetadataExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(_SUFFIX)

    def extract(self, path: Path):
        # fullName is the file stem: `<Type>.<Record>`.
        full_name = path.name[: -len(_SUFFIX)]
        if not full_name or "." not in full_name:
            # not a well-formed `<Type>.<Record>` name -> skip, never guess
            return [], []
        type_dev_name = full_name.split(".", 1)[0]
        if not type_dev_name:
            return [], []

        rid = f"custommetadatarecord/{full_name}"
        attrs: dict = {}
        root = None
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            root = None
        if root is not None:
            protected = _child_text(root, "protected").lower()
            if protected in ("true", "false"):
                attrs["protected"] = protected == "true"
        # NOTE: <label> and every <values>/<value> are DATA and never read.

        nodes = [node(rid, "custommetadatarecord", full_name, **attrs)]
        # The custom metadata TYPE object carries the `__mdt` suffix.
        edges = [raw_edge(rid, "references", "object", f"{type_dev_name}__mdt")]
        return nodes, edges


EXTRACTORS = [CustomMetadataExtractor()]
