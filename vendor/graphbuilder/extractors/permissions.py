"""Custom Permissions and Custom Notification Types.

Two small, related access-metadata types:

  - ``*.customPermission-meta.xml``       -> node ``custompermission/<Name>``
  - ``*.customNotificationType-meta.xml`` -> node ``customnotificationtype/<Name>``

A custom permission may list others it depends on under ``<requiredPermission>``
children; each yields a ``requires`` edge to ``custompermission/<other>``.

Names and structural relationships only. The metadata ``label`` is display text
and is not emitted. The canonical name for both types is the file's API name,
which the platform requires to equal the file basename, so the basename is taken
and no value/body text is read.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..xmlutil import local_name as _local

_CUSTOM_PERMISSION_SUFFIX = ".customPermission-meta.xml"
_CUSTOM_NOTIFICATION_SUFFIX = ".customNotificationType-meta.xml"


def _api_name(name: str, suffix: str) -> str:
    """API name = the file basename with the type suffix stripped."""
    return name[: -len(suffix)] if name.endswith(suffix) else name


def _required_permissions(root) -> list[str]:
    """Names listed under ``<requiredPermission>`` children of a custom
    permission. Matched by local name so a namespace prefix never hides them; each
    name appears once, in document order."""
    out: list[str] = []
    seen: set[str] = set()
    for el in root.iter():
        if _local(el.tag) != "requiredPermission":
            continue
        ref = (el.text or "").strip()
        if ref and ref not in seen:
            seen.add(ref)
            out.append(ref)
    return out


class CustomPermissionExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(_CUSTOM_PERMISSION_SUFFIX)

    def extract(self, path: Path):
        name = _api_name(path.name, _CUSTOM_PERMISSION_SUFFIX)
        nid = f"custompermission/{name}"
        nodes = [node(nid, "custompermission", name)]
        edges = []
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            return nodes, edges          # broken file: keep the node, skip refs
        for other in _required_permissions(root):
            edges.append(raw_edge(nid, "requires", "custompermission", other))
        return nodes, edges


class CustomNotificationTypeExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(_CUSTOM_NOTIFICATION_SUFFIX)

    def extract(self, path: Path):
        name = _api_name(path.name, _CUSTOM_NOTIFICATION_SUFFIX)
        nid = f"customnotificationtype/{name}"
        return [node(nid, "customnotificationtype", name)], []


EXTRACTORS = [CustomPermissionExtractor(), CustomNotificationTypeExtractor()]
