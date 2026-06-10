"""Security extractor — permission sets, profiles, permission-set groups.

Emits:
  - permissionset / profile  -> `grants`→object (objectPermissions) and
    `grants`→apexclass (classAccesses), plus the field-level and visibility
    grants below.
  - permissionsetgroup        -> `contains`→permissionset (group members).

Field/visibility grants:
  - fieldPermissions          -> `grants`→field (Object.Field), with
    readable/editable flags. The flat `field_grants` node attr is kept too.
  - tabVisibilities           -> `grants`→tab.
  - applicationVisibilities   -> `grants`→app (visible/default flags).
  - recordTypeVisibilities    -> `grants`→object, carrying a `record_type` attr.
  - customPermissions         -> `grants`→custompermission.
  - pageAccesses              -> `grants`→vfpage (Visualforce page access).
  - flowAccesses              -> `grants`→flow (run access).
  - customMetadataTypeAccesses / customSettingAccesses -> `grants`→object.

`parse_access` exposes only object/field/class names, so the readable/editable
flags and the visibility families are parsed here directly off the XML. Names
and structural relations only — no values, labels, or other content leave here.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..salesforce import parse_access, parse_permsetgroup
from ..xmlutil import child as _child, iter_local as _iter_local


def _t(el, tag):
    """Bare text of child ``tag`` under ``el`` (by local name), or "" if absent."""
    c = _child(el, tag)
    return c.text.strip() if c is not None and c.text else ""


def _is_true(el, tag):
    return _t(el, tag).lower() == "true"


def _root(path: Path):
    """Parse the file; return the root element or None on any read/parse error."""
    try:
        return ET.parse(path).getroot()
    except Exception:
        return None


class SecurityExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        n = path.name
        return (
            n.endswith(".permissionset-meta.xml")
            or n.endswith(".profile-meta.xml")
            or n.endswith(".permissionsetgroup-meta.xml")
        )

    def extract(self, path: Path):
        n = path.name
        if n.endswith(".permissionsetgroup-meta.xml"):
            return self._extract_group(path)
        kind = "permissionset" if n.endswith(".permissionset-meta.xml") else "profile"
        return self._extract_access(path, kind)

    def _extract_access(self, path: Path, kind: str):
        acc = parse_access(path, kind)
        aid = f"{kind}/{acc.name}"

        # Visibility/field families parsed straight off the XML (parse_access
        # exposes neither readable/editable flags nor the visibilities).
        field_access: dict[str, dict] = {}     # "Object.Field" -> {readable,editable}
        tabs: set[str] = set()
        apps: dict[str, dict] = {}             # app -> {visible, default}
        record_types: dict[str, set[str]] = {} # object -> {record type devnames}
        custom_perms: set[str] = set()
        pages: set[str] = set()                # pageAccesses -> vfpage
        flows: set[str] = set()                # flowAccesses -> flow
        data_objects: set[str] = set()         # customMetadataType/customSetting -> object

        root = _root(path)
        if root is not None:
            for fp_ in _iter_local(root, "fieldPermissions"):
                f = _t(fp_, "field")
                if f:
                    field_access[f] = {
                        "readable": _is_true(fp_, "readable"),
                        "editable": _is_true(fp_, "editable"),
                    }
            for tv in _iter_local(root, "tabVisibilities"):
                tab = _t(tv, "tab")
                if tab:
                    tabs.add(tab)
            for av in _iter_local(root, "applicationVisibilities"):
                app = _t(av, "application")
                if app:
                    apps[app] = {
                        "visible": _is_true(av, "visible"),
                        "default": _is_true(av, "default"),
                    }
            for rtv in _iter_local(root, "recordTypeVisibilities"):
                rt = _t(rtv, "recordType")        # "Object.RecordType"
                if rt and "." in rt:
                    obj, rname = rt.split(".", 1)
                    if obj and rname:
                        record_types.setdefault(obj, set()).add(rname)
            for cp in _iter_local(root, "customPermissions"):
                name = _t(cp, "name")
                if name:
                    custom_perms.add(name)
            for pa in _iter_local(root, "pageAccesses"):     # Visualforce pages
                pg = _t(pa, "apexPage")
                if pg:
                    pages.add(pg)
            for fa in _iter_local(root, "flowAccesses"):     # Flow (run access)
                fl = _t(fa, "flow")
                if fl:
                    flows.add(fl)
            # Custom Metadata Type / Custom Setting access both name an object
            # (the `__mdt` / `__c` entity); grant -> object like objectPermissions.
            for cmt in _iter_local(root, "customMetadataTypeAccesses"):
                nm = _t(cmt, "name")
                if nm:
                    data_objects.add(nm)
            for cs in _iter_local(root, "customSettingAccesses"):
                nm = _t(cs, "name")
                if nm:
                    data_objects.add(nm)

        nodes = [
            node(
                aid,
                kind,
                acc.label or acc.name,
                # field grants are KEPT as an attr (existing behavior) AND promoted
                # to edges below.
                field_grants=sorted(acc.fields),
                # Node-side mirror of the edge attrs so they survive resolution
                # (the core drops extra attrs off raw edges during pass 2).
                field_access={k: field_access[k] for k in sorted(field_access)},
                app_visibilities={a: apps[a] for a in sorted(apps)},
                record_type_visibilities={
                    o: sorted(record_types[o]) for o in sorted(record_types)
                },
            )
        ]

        edges = []
        # object / apexclass grants
        for obj in sorted(acc.objects):
            if obj:
                edges.append(raw_edge(aid, "grants", "object", obj))
        for cls in sorted(acc.classes):
            if cls:
                edges.append(raw_edge(aid, "grants", "apexclass", cls))

        # field-level grants -> field (Object.Field)
        for f in sorted(acc.fields):
            if not f:
                continue
            e = raw_edge(aid, "grants", "field", f)
            flags = field_access.get(f)
            if flags:
                e["readable"] = flags["readable"]
                e["editable"] = flags["editable"]
            edges.append(e)

        # --- tab visibilities -> tab -----------------------------------------
        for tab in sorted(tabs):
            edges.append(raw_edge(aid, "grants", "tab", tab))

        # --- application visibilities -> app (visible/default) ---------------
        for app in sorted(apps):
            e = raw_edge(aid, "grants", "app", app)
            e["visible"] = apps[app]["visible"]
            e["default"] = apps[app]["default"]
            edges.append(e)

        # --- record type visibilities -> object (record_type attr) -----------
        for obj in sorted(record_types):
            for rname in sorted(record_types[obj]):
                e = raw_edge(aid, "grants", "object", obj)
                e["record_type"] = rname
                edges.append(e)

        # --- custom permissions -> custompermission --------------------------
        for cp in sorted(custom_perms):
            edges.append(raw_edge(aid, "grants", "custompermission", cp))

        # --- Visualforce page access -> vfpage -------------------------------
        for pg in sorted(pages):
            edges.append(raw_edge(aid, "grants", "vfpage", pg))

        # --- Flow access -> flow ---------------------------------------------
        for fl in sorted(flows):
            edges.append(raw_edge(aid, "grants", "flow", fl))

        # --- custom metadata type / custom setting access -> object ----------
        for ob in sorted(data_objects):
            edges.append(raw_edge(aid, "grants", "object", ob))

        return nodes, edges

    def _extract_group(self, path: Path):
        psg = parse_permsetgroup(path)
        gid = f"permsetgroup/{psg.name}"
        nodes = [node(gid, "permsetgroup", psg.label or psg.name)]
        edges = []
        for ps in sorted(psg.permsets):
            if ps:
                edges.append(raw_edge(gid, "contains", "permissionset", ps))
        return nodes, edges


EXTRACTORS = [SecurityExtractor()]
