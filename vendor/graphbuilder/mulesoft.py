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

Phase-3 scope (richer taxonomy; everything additive — ids and the ``calls``/
``uses`` semantics above are frozen): a flow's *source trigger* (HTTP listener /
scheduler / generic connector source), its ``config-ref`` targets and ``${…}``
property reads, an APIkit-convention flow name decoded into method/path/config
(:func:`parse_apikit_flow_name`), and the file's top-level artifacts —
named global configs, ``<apikit:config>`` and the property files the app loads
(:func:`parse_artifacts`). Property KEYS only, never values. MUnit and DataWeave
stay out of scope (Phase 5).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# Mule namespace URIs look like ``http://www.mulesoft.org/schema/mule/<connector>/...``;
# the segment after this marker is the connector name (``core`` for built-ins).
MULE_SCHEMA = "www.mulesoft.org/schema/mule/"

# Standard Maven layout roots that scope a Mule app's config (Mule 4 / legacy 3).
_MULE_ROOTS = ("mule", "app")          # i.e. src/main/<root>/**/*.xml

# APIkit's generated-flow naming convention: <method>:\<resource>[:<content-type>]:<config>
# (resource-path slashes become backslashes, URI params `{x}` become `(x)`).
HTTP_METHODS = ("get", "post", "put", "delete", "patch", "options", "head", "trace")

# The APIkit module's namespace segment: `mule-apikit` (Mule 4) / `apikit` (Mule 3).
_APIKIT_CONNECTORS = ("mule-apikit", "apikit")

# A `${…}` configuration-property placeholder. Only static dotted keys are kept —
# anything else (expressions, unusual characters) is dynamic and is not guessed at.
_PROP_RE = re.compile(r"\$\{([^}]+)\}")
_KEY_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")


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


def _resources_index(parts: tuple) -> int:
    """Index ``i`` where ``parts[i:i+3]`` == ``src/main/resources``, or -1."""
    for i in range(len(parts) - 2):
        if parts[i] == "src" and parts[i + 1] == "main" and parts[i + 2] == "resources":
            return i
    return -1


def is_resources_path(path: Path) -> bool:
    """True for a file under the standard ``src/main/resources`` root (where a
    Mule app keeps its RAML specs and property files). Pure path-shape check."""
    return _resources_index(path.parts) != -1


def resource_rel_path(path: Path) -> str:
    """Path tail after ``src/main/resources``, else the bare filename — the form
    a ``<configuration-properties file=…>`` / ``<apikit:config api=…>`` attribute
    names the file by."""
    i = _resources_index(path.parts)
    if i == -1:
        return path.name
    return "/".join(path.parts[i + 3:]) or path.name


def apikit_path(encoded: str) -> str:
    """An APIkit flow-name resource segment back to its RAML form:
    ``\\orders\\(orderId)`` -> ``/orders/{orderId}``."""
    return encoded.replace("\\", "/").replace("(", "{").replace(")", "}")


def parse_apikit_flow_name(name: str) -> dict | None:
    """Decode an APIkit-convention flow name into its parts, or ``None``.

    ``get:\\orders:orders-config``                    -> method/path/config
    ``post:\\orders:application\\json:orders-config`` -> + content type

    Returns ``{"method", "path", "ctype", "config"}`` with ``path`` translated to
    RAML form (:func:`apikit_path`). Anything that doesn't match the convention
    (including dynamic or hand-named flows) returns ``None`` — never guessed."""
    parts = name.split(":")
    if len(parts) not in (3, 4):
        return None
    method, resource, config = parts[0].lower(), parts[1], parts[-1]
    if method not in HTTP_METHODS or not resource.startswith("\\") or not config:
        return None
    ctype = parts[2].replace("\\", "/") if len(parts) == 4 else ""
    return {"method": method, "path": apikit_path(resource), "ctype": ctype,
            "config": config}


def spec_name(value: str) -> str:
    """Normalize an ``<apikit:config raml=…/api=…>`` value to the spec's file
    name: ``api/orders.raml`` and ``resource::com.acme:orders-api:1.0.1:raml:zip:
    orders.raml`` both -> ``orders.raml`` (the form RAML spec nodes are keyed by)."""
    v = value.split(":")[-1] if ":" in value else value
    return v.replace("\\", "/").rsplit("/", 1)[-1].strip()


def prop_keys_of(value: str) -> set:
    """The static ``${…}`` property keys referenced inside one attribute value.
    Dynamic placeholders (expressions, unexpected characters) are skipped, never
    guessed; a ``secure::`` prefix is stripped (the key itself is what's read)."""
    keys = set()
    for m in _PROP_RE.finditer(value):
        k = m.group(1).strip()
        if k.startswith("secure::"):
            k = k[len("secure::"):]
        if _KEY_RE.fullmatch(k):
            keys.add(k)
    return keys


def _element_prop_keys(el) -> set:
    """Static ``${…}`` keys in the attributes of ``el`` and all its descendants."""
    keys: set = set()
    for d in el.iter():
        for v in d.attrib.values():
            keys |= prop_keys_of(v)
    return keys


@dataclass
class MuleFlow:
    name: str
    kind: str = "flow"                            # flow | sub-flow
    file: str = ""                                # rel path under src/main/mule
    refs: set = field(default_factory=set)        # <flow-ref> target names
    connectors: set = field(default_factory=set)  # connector namespaces used
    # ---- Phase 3 (all default-empty, so Phase-1 callers are unaffected) ----
    api: dict | None = None                       # decoded APIkit name, or None
    source: dict | None = None                    # first-child source trigger, or None
    config_refs: set = field(default_factory=set)   # global-config names used
    apikit_refs: set = field(default_factory=set)   # <apikit:config> names used
    routers: set = field(default_factory=set)       # apikit_refs that are <apikit:router>
    prop_reads: set = field(default_factory=set)    # static ${…} keys read


def _source_of(flow_el) -> dict | None:
    """The flow's *source trigger*, decoded from its first child element — Mule
    puts a flow's source first; a flow whose first child is a plain processor
    (e.g. an APIkit-generated flow starting with a transform) has none.

    Recognized shapes (anything else is not guessed):
      - ``<http:listener path=…>``       -> ``{"kind": "httplistener", "path", "config"}``
      - ``<scheduler>``                  -> ``{"kind": "scheduler", "frequency"|"cron"}``
      - any other ``<*:listener>`` or ``<*:on-…>`` (vm/jms/sftp/file/db polling
        sources) -> ``{"kind": "source", "connector", "element", "config"}``
    """
    for child in flow_el:                          # first element child only
        ln = local_name(child.tag)
        conn = connector_of(child.tag)
        if ln == "scheduler":
            info = {"kind": "scheduler"}
            for d in child.iter():
                dn = local_name(d.tag)
                if dn == "fixed-frequency" and d.get("frequency"):
                    info["frequency"] = d.get("frequency")
                elif dn == "cron" and d.get("expression"):
                    info["cron"] = d.get("expression")
            return info
        if ln == "listener" and conn == "http":
            return {"kind": "httplistener", "path": child.get("path", ""),
                    "config": child.get("config-ref", "")}
        if ln == "listener" or ln.startswith("on-"):
            return {"kind": "source", "connector": conn or "core", "element": ln,
                    "config": child.get("config-ref", "")}
        return None
    return None


def parse_config(path: Path) -> list[MuleFlow]:
    """Flows + sub-flows defined in one Mule config file.

    Returns ``[]`` (never raises) when the file does not parse or its root local
    tag is not ``mule`` — so a non-Mule XML that happens to sit under the config
    root is skipped cleanly. For each ``<flow>`` / ``<sub-flow>`` with a ``name``,
    walks its descendants to collect ``<flow-ref>`` targets, the connector
    namespace of every namespaced processor (excluding ``core``), and the Phase-3
    fields: ``config-ref`` targets (APIkit configs kept apart from global
    configs), static ``${…}`` property reads, the source trigger, and the decoded
    APIkit name when the flow follows the generated-flow convention."""
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
        mf = MuleFlow(name=name, kind=ln, file=rel,
                      api=parse_apikit_flow_name(name), source=_source_of(el))
        for d in el.iter():                       # includes el itself + all descendants
            dn = local_name(d.tag)
            c = connector_of(d.tag)
            if dn == "flow-ref" and d.get("name"):
                mf.refs.add(d.get("name"))
            elif c and c != "core":
                mf.connectors.add(c)
            ref = d.get("config-ref")
            if ref and d is not el:
                if c in _APIKIT_CONNECTORS:        # router/console -> an APIkit config
                    mf.apikit_refs.add(ref)
                    if dn == "router":
                        mf.routers.add(ref)
                else:
                    mf.config_refs.add(ref)
            for v in d.attrib.values():
                mf.prop_reads |= prop_keys_of(v)
        flows.append(mf)
    return flows


# --------------------------------------------------------------------------- #
# Phase 3 — top-level (non-flow) artifacts of one config file
# --------------------------------------------------------------------------- #
@dataclass
class MuleGlobal:
    """A named top-level config element (``<http:listener-config name=…>``,
    ``<db:config name=…>``, ``<secure-properties:config name=…>``, …)."""
    name: str
    element: str = ""                             # "<connector>:<localname>"
    file: str = ""                                # rel path under src/main/mule
    prop_keys: set = field(default_factory=set)   # static ${…} keys it reads
    config_refs: set = field(default_factory=set)  # other global configs it refs
    props_file: str = ""                          # file= it loads (secure-properties)


@dataclass
class ApikitConfig:
    """An ``<apikit:config name=… raml=…/api=…>`` declaration."""
    name: str
    spec: str = ""                                # normalized spec file name
    file: str = ""


@dataclass
class MuleArtifacts:
    """Everything top-level in one config file that isn't a flow."""
    globals: list = field(default_factory=list)        # MuleGlobal
    apikit_configs: list = field(default_factory=list)  # ApikitConfig
    property_files: list = field(default_factory=list)  # <configuration-properties file=…>
    prop_keys: set = field(default_factory=set)          # ${…} read in those file names


def parse_artifacts(path: Path) -> MuleArtifacts:
    """Top-level artifacts of one Mule config file (never raises; empty result
    for a non-Mule or broken file — same contract as :func:`parse_config`).

    Collects ``<configuration-properties file=…>`` loads (the raw attribute is
    kept verbatim — a ``${env}``-parameterized name is dynamic and stays visible
    rather than being guessed), ``<apikit:config>`` declarations with their spec
    file normalized via :func:`spec_name`, and every other *named* top-level
    element as a :class:`MuleGlobal` (with the property keys and ``config-ref``
    targets found anywhere inside it). Property keys only — values are never
    captured."""
    arts = MuleArtifacts()
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return arts
    if local_name(root.tag) != "mule":
        return arts
    rel = rel_path(path)
    for el in root:
        ln = local_name(el.tag)
        conn = connector_of(el.tag)
        name = el.get("name")
        if ln in ("flow", "sub-flow"):
            continue
        if ln == "configuration-properties":
            f = el.get("file", "")
            if f:
                arts.property_files.append(f)
                arts.prop_keys |= prop_keys_of(f)
            continue
        if conn in _APIKIT_CONNECTORS and ln == "config" and name:
            raw = el.get("api") or el.get("raml") or ""
            arts.apikit_configs.append(
                ApikitConfig(name=name, spec=spec_name(raw) if raw else "", file=rel))
            continue
        if not name:                              # unnamed top-level — not addressable
            continue
        g = MuleGlobal(name=name, element=f"{conn}:{ln}" if conn else ln, file=rel,
                       prop_keys=_element_prop_keys(el),
                       props_file=el.get("file", "") if conn == "secure-properties" else "")
        for d in el.iter():
            ref = d.get("config-ref")
            if ref and d is not el:
                g.config_refs.add(ref)
        arts.globals.append(g)
    return arts
