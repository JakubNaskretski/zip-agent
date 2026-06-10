"""Reports & Dashboards — analytics metadata.

Owns `*.report-meta.xml` and `*.dashboard-meta.xml`. Emits:
  Reports:
    - node `report/<Name>` (Name = the filename minus the suffix; never the title)
    - `<reportType>` base object -> `on` -> object/<Object>
    - `<columns><field>` and grouping `<field>` tokens -> `reads` -> field/<Object>.<Field>,
      but only when the field's object is determinable (an explicit `Object.Field`
      relationship); a bare/standard column token is skipped rather than guessed.
  Dashboards:
    - node `dashboard/<Name>`
    - each component / filter referencing a report -> `uses` -> report/<ReportName>
      (the report is named by the trailing segment of its folder/API path).

Structural names and relationships only. Report/dashboard filters, criteria, chart
data, and label/title text are never read — only element names (`<reportType>`,
`<columns><field>`, `<groupingsDown>/<Across><field>`, `<dashboardComponents><report>`).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..xmlutil import child as _child, iter_local as _iter_local

# reportType tokens that are not real SObjects (tabular/standard wrappers) — when
# the base object isn't a determinable SObject we skip the `on` edge rather than
# emit a bogus object. These are lower-cased for comparison.
_NON_OBJECT_REPORTTYPES = {"", "tabular", "summary", "matrix", "joined"}


def _name_from_path(path: Path, suffix: str) -> str:
    """Report/dashboard API name = filename minus the meta suffix (NOT the title).

    Folders (e.g. ``unfiled$public/MyReport.report-meta.xml``) carry the folder
    in the path, not the api name; the bare stem is the resolvable name.
    """
    n = path.name
    return n[: -len(suffix)] if n.endswith(suffix) else path.stem


def _base_object_from_reporttype(report_type: str) -> str | None:
    """Best-effort SObject for a ``<reportType>`` token.

    The leading dotted segment of a report type names its base object for both
    standard (``Opportunity``, ``Account``) and custom report types
    (``MyObject__c``). A bare standard wrapper token with no determinable object
    (``Tabular``, etc.) yields None — caller skips the edge rather than guess.
    """
    if not report_type:
        return None
    head = report_type.split(".", 1)[0].strip()
    if not head or head.lower() in _NON_OBJECT_REPORTTYPES:
        return None
    return head


def _split_field_token(token: str) -> tuple[str, str] | None:
    """Best-effort split of a report field token into (Object, Field).

    Only a token whose object is *determinable* yields a result; everything else
    is skipped (returns None) so we never guess an object:

      - ``Account.Name`` / ``MyObj__c.MyField__c``  -> ("Account", "Name") etc.
        (relationship paths like ``Account.Owner.Name`` collapse to the root
        object + the final field segment — the determinable endpoints.)
      - bare standard column tokens (``OPPORTUNITY_TYPE``), bucket fields
        (``BucketField_...``), custom summary formulas (``FORMULA1``) and any
        other object-less token -> None (skipped).
    """
    if not token:
        return None
    token = token.strip()
    if "." not in token:
        return None                       # no object segment -> not determinable
    parts = [p for p in token.split(".") if p]
    if len(parts) < 2:
        return None
    obj, field = parts[0], parts[-1]
    if not obj or not field:
        return None
    return obj, field


def _report_name_from_ref(ref: str) -> str | None:
    """A dashboard's ``<report>`` is an API path like ``FolderName/MyReport``;
    the resolvable report name is the trailing segment. Returns None if empty."""
    if not ref:
        return None
    name = ref.strip().rstrip("/").split("/")[-1]
    return name or None


class ReportExtractor:
    source = "salesforce"

    _REPORT_SUFFIX = ".report-meta.xml"
    _DASHBOARD_SUFFIX = ".dashboard-meta.xml"

    def handles(self, path: Path) -> bool:
        n = path.name
        return n.endswith(self._REPORT_SUFFIX) or n.endswith(self._DASHBOARD_SUFFIX)

    def extract(self, path: Path):
        if path.name.endswith(self._DASHBOARD_SUFFIX):
            return self._extract_dashboard(path)
        return self._extract_report(path)

    # --- reports ----------------------------------------------------------- #
    def _extract_report(self, path: Path):
        name = _name_from_path(path, self._REPORT_SUFFIX)
        rid = f"report/{name}"
        nodes = [node(rid, "report", name)]
        edges: list[dict] = []

        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            return nodes, edges        # broken XML -> bare node, no edges

        # base object from <reportType> (top-level only)
        base_obj = None
        rt = _child(root, "reportType")
        if rt is not None and rt.text:
            base_obj = _base_object_from_reporttype(rt.text)
            if base_obj:
                edges.append(raw_edge(rid, "on", "object", base_obj))

        # <columns><field> and grouping <field> tokens -> reads -> field.
        # Only <field> elements under <columns>, <groupingsDown> and
        # <groupingsAcross> are scanned; filters/criteria are never read.
        seen: set[str] = set()
        for container_tag in ("columns", "groupingsDown", "groupingsAcross"):
            for container in _iter_local(root, container_tag):
                for fld in _iter_local(container, "field"):
                    txt = fld.text
                    if not txt:
                        continue
                    split = _split_field_token(txt)
                    if split is None:
                        continue          # object not determinable -> skip, don't guess
                    obj, field = split
                    fq = f"{obj}.{field}"
                    if fq in seen:
                        continue
                    seen.add(fq)
                    edges.append(raw_edge(rid, "reads", "field", fq))

        return nodes, edges

    # --- dashboards -------------------------------------------------------- #
    def _extract_dashboard(self, path: Path):
        name = _name_from_path(path, self._DASHBOARD_SUFFIX)
        did = f"dashboard/{name}"
        nodes = [node(did, "dashboard", name)]
        edges: list[dict] = []

        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            return nodes, edges

        # components and dashboard filters reference a report by API path.
        # <report> appears inside <dashboardComponents> and <dashboardFilters>;
        # iterating all <report> elements captures both. Filter VALUES (the
        # operator/column-value children) are ignored — only the report name.
        seen: set[str] = set()
        for rep in _iter_local(root, "report"):
            rep_name = _report_name_from_ref(rep.text or "")
            if rep_name is None or rep_name in seen:
                continue
            seen.add(rep_name)
            edges.append(raw_edge(did, "uses", "report", rep_name))

        return nodes, edges


EXTRACTORS = [ReportExtractor()]
