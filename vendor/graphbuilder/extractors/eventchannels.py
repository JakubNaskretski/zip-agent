"""Platform Event / Change-Data-Capture channel extractor.

Change Data Capture (CDC) and custom Platform Event streaming are wired through
two metadata types:

  - `*.platformEventChannel-meta.xml` — the channel itself::

        <PlatformEventChannel xmlns="http://soap.sforce.com/2006/04/metadata">
          <channelType>data</channelType>
          <label>Sales Changes</label>
        </PlatformEventChannel>

  - `*.platformEventChannelMember-meta.xml` — binds one entity to a channel::

        <PlatformEventChannelMember xmlns="...">
          <eventChannel>Sales_Changes__chn</eventChannel>
          <selectedEntity>Account</selectedEntity>
        </PlatformEventChannelMember>

The channel becomes a `platformeventchannel/<Name>` node. Each member adds a
`references` edge `platformeventchannel/<eventChannel>` -> `object/<selectedEntity>`
(the object whose changes flow on the channel), and re-emits the channel node so
the link forms even when only the member file was retrieved.

Scope is structural only: channel/object NAMES and the `channelType` flag. No
values are read. Parsing is namespace-agnostic and never raises on odd input.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..xmlutil import child_text as _child_text

_CHANNEL_SUFFIX = ".platformEventChannel-meta.xml"
_MEMBER_SUFFIX = ".platformEventChannelMember-meta.xml"


class EventChannelExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        # Member suffix is the longer, more specific one — check it first.
        return path.name.endswith(_MEMBER_SUFFIX) or path.name.endswith(_CHANNEL_SUFFIX)

    def extract(self, path: Path):
        if path.name.endswith(_MEMBER_SUFFIX):
            return self._extract_member(path)
        return self._extract_channel(path)

    def _extract_channel(self, path: Path):
        name = path.name[: -len(_CHANNEL_SUFFIX)]
        if not name:
            return [], []
        attrs: dict = {}
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            root = None
        if root is not None:
            ctype = _child_text(root, "channelType")
            if ctype:
                attrs["channel_type"] = ctype
        return [node(f"platformeventchannel/{name}", "platformeventchannel", name, **attrs)], []

    def _extract_member(self, path: Path):
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            return [], []
        channel = _child_text(root, "eventChannel")
        entity = _child_text(root, "selectedEntity")
        if not channel:
            return [], []
        cid = f"platformeventchannel/{channel}"
        # Re-emit the channel node so the binding forms even if only the member
        # file is present (setdefault in the core dedups against the channel file).
        nodes = [node(cid, "platformeventchannel", channel)]
        edges = []
        if entity:
            edges.append(raw_edge(cid, "references", "object", entity))
        return nodes, edges


EXTRACTORS = [EventChannelExtractor()]
