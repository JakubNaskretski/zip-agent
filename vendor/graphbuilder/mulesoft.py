"""MuleSoft parsers — turn a Mule app's config XML into typed flow records.

A Mule application is a static tree of XML config under ``src/main/mule`` (Mule 4)
or ``src/main/app`` (legacy Mule 3) — so, like a Salesforce ``force-app``, it is
parsed straight off disk with no remote *collect* step. Parsing is dependency-free
(stdlib ``xml.etree``) and namespace-tolerant: Mule uses one XML namespace per
connector, so we work off local tag names and the connector segment of the
namespace URI rather than any fixed prefix.

Phase-1 scope (back-compat parity with the hand-rolled zip-agent digest):
``<flow>`` / ``<sub-flow>`` become :class:`MuleFlow` records carrying their
``<flow-ref>`` targets and the connector namespaces they use. Sub-flows are kept
as flows with ``kind="sub-flow"`` (not a separate type) so a ``<flow-ref>`` target
id is always ``muleflow/<name>`` — resolution never needs to know the target kind.
Richer artifacts (APIkit/RAML, properties, pom, MUnit, DataWeave) are deliberately
out of scope here and land in later phases.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# Mule namespace URIs look like ``http://www.mulesoft.org/schema/mule/<connector>/...``;
# the segment after this marker is the connector name (``core`` for built-ins).
MULE_SCHEMA = "www.mulesoft.org/schema/mule/"

# Standard Maven layout roots that scope a Mule app's config (Mule 4 / legacy 3).
_MULE_ROOTS = ("mule", "app")          # i.e. src/main/<root>/**/*.xml


def local_name(tag: str) -> str:
    """The local tag name, dropping any ``{namespace}`` prefix."""
    return tag.rsplit("}", 1)[-1]


def connector_of(tag: str) -> str:
    """Connector name from a Mule namespace URI (``…/mule/db`` -> ``db``); ``""``
    if the tag carries no Mule namespace."""
    if not tag.startswith("{"):
        return ""
    uri = tag[1:].split("}", 1)[0]
    if MULE_SCHEMA in uri:
        return uri.split(MULE_SCHEMA, 1)[1].split("/")[0]
    return ""


def _mule_root_index(parts: tuple) -> int:
    """Index ``i`` where ``parts[i:i+3]`` == ``src/main/{mule,app}``, or -1."""
    for i in range(len(parts) - 2):
        if parts[i] == "src" and parts[i + 1] == "main" and parts[i + 2] in _MULE_ROOTS:
            return i
    return -1


def is_config_path(path: Path) -> bool:
    """True for an ``.xml`` file under a standard Mule config root. A pure
    path-shape check (no I/O), so it stays cheap when offered non-Mule files
    during, e.g., a Salesforce build — force-app metadata never lives under
    ``src/main/mule``. A stray non-Mule XML that *does* sit there is harmless:
    :func:`parse_config` returns ``[]`` for it (root tag check)."""
    return path.suffix == ".xml" and _mule_root_index(path.parts) != -1


def rel_path(path: Path) -> str:
    """Path tail after the ``src/main/{mule,app}`` root, else the bare filename —
    self-contained provenance for a flow node without needing the repo root."""
    i = _mule_root_index(path.parts)
    if i == -1:
        return path.name
    return "/".join(path.parts[i + 3:]) or path.name


@dataclass
class MuleFlow:
    name: str
    kind: str = "flow"                            # flow | sub-flow
    file: str = ""                                # rel path under src/main/mule
    refs: set = field(default_factory=set)        # <flow-ref> target names
    connectors: set = field(default_factory=set)  # connector namespaces used


def parse_config(path: Path) -> list[MuleFlow]:
    """Flows + sub-flows defined in one Mule config file.

    Returns ``[]`` (never raises) when the file does not parse or its root local
    tag is not ``mule`` — so a non-Mule XML that happens to sit under the config
    root is skipped cleanly. For each ``<flow>`` / ``<sub-flow>`` with a ``name``,
    walks its descendants to collect ``<flow-ref>`` targets and the connector
    namespace of every namespaced processor (excluding ``core``)."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    if local_name(root.tag) != "mule":
        return []
    rel = rel_path(path)
    flows: list[MuleFlow] = []
    for el in root:                               # flows/sub-flows are top-level
        ln = local_name(el.tag)
        name = el.get("name")
        if ln not in ("flow", "sub-flow") or not name:
            continue
        mf = MuleFlow(name=name, kind=ln, file=rel)
        for d in el.iter():                       # includes el itself + all descendants
            if local_name(d.tag) == "flow-ref" and d.get("name"):
                mf.refs.add(d.get("name"))
                continue
            c = connector_of(d.tag)
            if c and c != "core":
                mf.connectors.add(c)
        flows.append(mf)
    return flows
