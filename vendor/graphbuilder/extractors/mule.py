"""MuleSoft application config extractor (``src/main/mule/**/*.xml``).

For each Mule config file:
  - every ``<flow>`` / ``<sub-flow>`` becomes a ``muleflow`` node (attrs ``kind`` =
    flow|sub-flow, ``file`` = rel path);
  - a ``<flow-ref>`` becomes a ``calls`` edge (``muleflow`` -> ``muleflow``); an
    undefined target resolves to an ``external`` stub, exactly like a cross-file
    Salesforce reference;
  - a connector namespace a flow uses becomes a ``muleconnector`` node + a ``uses``
    edge.

Those two node types and two edge types are FROZEN for back-compat with the
zip-agent runtime helpers (``who_calls`` / ``connectors_used`` / …). Everything
below is the additive Phase-3 taxonomy:

  - **APIkit**: a flow named by the generated-flow convention
    (``get:\\orders:cfg``) carries ``api_method``/``api_path``/``api_config``
    attrs and an ``implements`` edge to its ``apiresource`` (the RAML extractor
    declares those; an unmatched path becomes an external stub — a visible
    diagnostic, not a guess). The same decoded name wires ``routesto``
    (``apikitrouter/<cfg>`` -> the flow): both ids derive from the flow's own
    name, so no cross-file resolver is needed. The router element itself emits
    the ``apikitrouter`` node, a ``contains`` edge from its host flow and a
    ``usesconfig`` edge to its ``apikitconfig``; ``<apikit:config>`` emits the
    ``apikitconfig`` node and its ``boundto`` edge to the ``apispec``.
  - **Source triggers**: a flow's first-child source becomes ``exposedby`` ->
    ``httplistener/<path>`` (HTTP), ``triggeredby`` -> ``scheduler/<flow>`` or
    ``mulesource/<connector>:<element>`` (anything else recognized). The flow
    node also carries ``source_kind``/``source_path``/… attrs for direct lookup.
  - **Configuration**: ``config-ref`` -> ``usesconfig`` edges (flow ->
    ``globalconfig``, or -> ``apikitconfig`` for APIkit elements); static
    ``${key}`` placeholders -> ``reads`` edges to ``propertykey`` nodes (KEYS
    only, values never captured); named top-level elements -> ``globalconfig``
    nodes with their own ``reads``/``usesconfig`` edges;
    ``<configuration-properties file=…>`` -> a ``loads`` edge from the
    app-singleton ``muleartifactdescriptor/app`` to the ``propertyfile``.

Like Salesforce, this is a static on-disk tree — no collect step. The property
files / RAML specs / pom referenced here are parsed by their own extractors
(``muleprops`` / ``raml`` / ``mulebuild``); unreferenced or missing targets
resolve to external stubs.
"""
from __future__ import annotations

from pathlib import Path

from ..core import node, raw_edge
from ..mulesoft import is_config_path, parse_artifacts, parse_config

# One Mule app per built tree (the same assumption `muleflow/<name>` ids make),
# so the app/descriptor node is a per-build singleton every emitter can share.
APP_ID = "muleartifactdescriptor/app"


def app_node() -> dict:
    """The minimal app-singleton node; any Mule extractor may emit it (the node
    registry keeps the first emission, so duplicates are harmless)."""
    return node(APP_ID, "muleartifactdescriptor", "app")


class MuleConfigExtractor:
    source = "mule"

    def handles(self, path: Path) -> bool:
        return is_config_path(path)

    def extract(self, path: Path):
        nodes: list[dict] = []
        edges: list[dict] = []
        for f in parse_config(path):
            fid = f"muleflow/{f.name}"
            attrs = {"kind": f.kind, "file": f.file}
            if f.api:
                attrs.update(api_method=f.api["method"], api_path=f.api["path"],
                             api_config=f.api["config"])
            if f.source:
                attrs["source_kind"] = f.source["kind"]
                if f.source.get("path") is not None:
                    attrs["source_path"] = f.source["path"]
                if f.source.get("config"):
                    attrs["source_config"] = f.source["config"]
            nodes.append(node(fid, "muleflow", f.name, **attrs))
            for c in sorted(f.connectors):
                nodes.append(node(f"muleconnector/{c}", "muleconnector", c))
                edges.append(raw_edge(fid, "uses", "muleconnector", c))
            for ref in sorted(f.refs):
                edges.append(raw_edge(fid, "calls", "muleflow", ref))
            # ---- Phase 3: APIkit-named flow -> resource + router wiring ----
            if f.api:
                edges.append(raw_edge(fid, "implements", "apiresource", f.api["path"]))
                rid = f"apikitrouter/{f.api['config']}"
                nodes.append(node(rid, "apikitrouter", f.api["config"]))
                edges.append(raw_edge(rid, "routesto", "muleflow", f.name))
            # ---- Phase 3: source trigger ----
            if f.source:
                s = f.source
                if s["kind"] == "httplistener":
                    pname = s.get("path") or s.get("config") or f.name
                    nodes.append(node(f"httplistener/{pname}", "httplistener", pname,
                                      config=s.get("config", "")))
                    edges.append(raw_edge(fid, "exposedby", "httplistener", pname))
                elif s["kind"] == "scheduler":
                    sched = {k: s[k] for k in ("frequency", "cron") if k in s}
                    nodes.append(node(f"scheduler/{f.name}", "scheduler", f.name, **sched))
                    edges.append(raw_edge(fid, "triggeredby", "scheduler", f.name))
                else:
                    sname = f"{s['connector']}:{s['element']}"
                    nodes.append(node(f"mulesource/{sname}", "mulesource", sname))
                    edges.append(raw_edge(fid, "triggeredby", "mulesource", sname))
            # ---- Phase 3: config refs + property reads ----
            for ref in sorted(f.config_refs):
                edges.append(raw_edge(fid, "usesconfig", "globalconfig", ref))
            for ref in sorted(f.apikit_refs):
                edges.append(raw_edge(fid, "usesconfig", "apikitconfig", ref))
            for ref in sorted(f.routers):
                rid = f"apikitrouter/{ref}"
                nodes.append(node(rid, "apikitrouter", ref, file=f.file, flow=f.name))
                edges.append(raw_edge(fid, "contains", "apikitrouter", ref))
                edges.append(raw_edge(rid, "usesconfig", "apikitconfig", ref))
            for key in sorted(f.prop_reads):
                edges.append(raw_edge(fid, "reads", "propertykey", key))

        # ---- Phase 3: top-level (non-flow) artifacts of this file ----
        arts = parse_artifacts(path)
        for a in arts.apikit_configs:
            cid = f"apikitconfig/{a.name}"
            nodes.append(node(cid, "apikitconfig", a.name, file=a.file, spec=a.spec))
            if a.spec:
                edges.append(raw_edge(cid, "boundto", "apispec", a.spec))
        for g in arts.globals:
            gid = f"globalconfig/{g.name}"
            nodes.append(node(gid, "globalconfig", g.name, element=g.element, file=g.file))
            for key in sorted(g.prop_keys):
                edges.append(raw_edge(gid, "reads", "propertykey", key))
            for ref in sorted(g.config_refs):
                edges.append(raw_edge(gid, "usesconfig", "globalconfig", ref))
            if g.props_file:                      # <secure-properties:config file=…>
                edges.append(raw_edge(gid, "loads", "propertyfile", g.props_file))
        if arts.property_files or arts.prop_keys:
            nodes.append(app_node())
        for pf in arts.property_files:
            edges.append(raw_edge(APP_ID, "loads", "propertyfile", pf))
        for key in sorted(arts.prop_keys):        # e.g. the `env` in config-${env}.yaml
            edges.append(raw_edge(APP_ID, "reads", "propertykey", key))
        return nodes, edges


EXTRACTORS = [MuleConfigExtractor()]
