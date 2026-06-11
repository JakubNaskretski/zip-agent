"""Graph model — the shared node/edge vocabulary plus depth-limited, cycle-safe
traversal.

``NODE_TYPES`` / ``EDGE_TYPES`` are the vocabulary every extractor emits against.
The traversal helpers stay robust on cyclic or broken metadata: every walk is
bounded (an explicit depth limit) and terminating (a visited set), so a reference
cycle (A -> B -> A) can never loop forever. ``find_cycles`` is purely diagnostic
and never influences ``traverse``.
"""
from __future__ import annotations

from collections import deque

# ---- node + edge vocabulary ----
NODE_TYPES = {
    "object", "field", "apexclass", "apexmethod", "trigger", "flow", "flowelement",
    "lwc", "flexipage", "permissionset", "profile", "permsetgroup",
    "omniscript", "integrationprocedure", "datamapper", "flexcard",
    "label", "approvalprocess", "sharingrule", "app", "tab", "recordtype",
    "aura", "vfpage", "vfcomponent", "quickaction", "layout",
    "queue", "publicgroup", "role", "emailtemplate", "emailalert", "report",
    "dashboard", "custompermission", "customnotificationtype",
    "assignmentrule", "escalationrule", "duplicaterule", "matchingrule",
    # data-model nodes carry structural names only — never field/record values
    "custommetadatarecord", "globalvalueset", "listview", "platformeventchannel",
    # external-only edge targets (referenced, never retrieved)
    "resource", "messagechannel",
    # ---- Confluence source (a second, SEPARATE graph; see graphbuilder/confluence) ----
    # Structural names plus page body text — a deliberate content-capture exception to
    # the names-only rule, so Confluence outputs are sensitive-by-default and gitignored.
    "space", "page", "attachment", "confluencelabel", "confluenceuser",
    # ---- Jira source (a third, SEPARATE graph; see graphbuilder/jira) ----
    # Same content-capture exception: issue nodes carry the description text inline.
    "jiraproject", "jiraissue", "jirauser", "jiralabel",
    # ---- MuleSoft source (a fourth, SEPARATE graph; see graphbuilder/mulesoft +
    # extractors/mule). Structural names only (no content capture) — a static
    # src/main/mule XML tree, parsed like a force-app with no remote collect.
    # `calls` (flow->flow via <flow-ref>) and `uses` (flow->connector) reuse the
    # existing edge vocabulary. ----
    "muleflow", "muleconnector",
    # Phase-3 Mule taxonomy. APIkit/API surface (router/config/spec/resource),
    # flow source triggers (HTTP listener / scheduler / generic connector source),
    # configuration (property files + KEYS ONLY — property values are never
    # captured, per the names-only rule — and named global configs), and build
    # metadata (pom dependencies + the app descriptor; `muleartifactdescriptor/app`
    # is a per-build singleton, mirroring the one-app-per-tree assumption that
    # `muleflow/<name>` ids already make).
    "apikitrouter", "apikitconfig", "apispec", "apiresource",
    "httplistener", "scheduler", "mulesource",
    "propertyfile", "propertykey", "globalconfig",
    "pomdependency", "muleartifactdescriptor",
}
EDGE_TYPES = {
    "field_of", "lookup", "on", "calls", "references", "touches", "uses",
    "uses-component", "page-for", "embeds", "grants", "contains", "maps",
    "extends", "implements", "invocable", "aura-enabled", "wire",
    "reads", "writes", "subflow", "async", "validates", "formula",
    "tests", "requires",
    # ---- Confluence / Jira: intra-source structure, plus `documents` (the
    # cross-source join; never emitted by a build) ----
    "child-of", "links-to", "attaches", "labeled", "mentions", "authored-by",
    "assigned-to", "documents",
    # ---- MuleSoft Phase-3 taxonomy (`reads`, `implements` and `contains` are
    # reused from the shared vocabulary above): APIkit routing (router->flow /
    # config->spec / spec->resource), source triggers (flow->listener|source),
    # properties (app|config->file, file->key), config refs, build deps. ----
    "routesto", "boundto", "declares", "exposedby", "triggeredby",
    "loads", "defineskey", "usesconfig", "securedby", "dependson",
}


def index(graph):
    """Build lookup structures for fast traversal.

    Returns ``(nodes_by_id, out_adjacency, in_adjacency)`` where each adjacency
    maps a node id to a list of ``(edge_type, neighbour_id)`` pairs — outgoing
    edges in ``out_adjacency``, incoming edges in ``in_adjacency``.
    """
    nodes_by_id = {n["id"]: n for n in graph["nodes"]}
    out_adj: dict[str, list[tuple[str, str]]] = {}
    in_adj: dict[str, list[tuple[str, str]]] = {}
    for e in graph["edges"]:
        out_adj.setdefault(e["src"], []).append((e["type"], e["dst"]))
        in_adj.setdefault(e["dst"], []).append((e["type"], e["src"]))
    return nodes_by_id, out_adj, in_adj


def traverse(graph, start, direction="out", max_depth=1, edge_types=None):
    """Depth-limited, cycle-safe breadth-first walk from ``start``.

    ``direction`` selects which edges are followed: ``"out"`` (outgoing) or
    ``"in"`` (incoming) walk that one direction; ``"any"`` walks edges in either
    direction in a single BFS (undirected distance — a node two hops away through
    "out then in" is reached at depth 2). Returns ``[(node_id, depth,
    via_edge_type), …]`` for every node reached, excluding ``start`` itself.

    ``max_depth=None`` makes the walk unbounded but it stays terminating thanks to
    the visited set. ``edge_types`` (a set) restricts which edge types are
    followed. Because every neighbour is recorded in ``visited`` before it is
    enqueued, a reference cycle can never cause an infinite loop.
    """
    _, out_adj, in_adj = index(graph)
    if direction == "out":
        adjacency = out_adj
    elif direction == "in":
        adjacency = in_adj
    else:  # "any" — undirected: combine outgoing + incoming neighbours
        adjacency = {}
        for src, neighbours in out_adj.items():
            adjacency.setdefault(src, []).extend(neighbours)
        for src, neighbours in in_adj.items():
            adjacency.setdefault(src, []).extend(neighbours)

    visited = {start}
    reached: list[tuple[str, int, str]] = []
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    while queue:
        current, depth = queue.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        for edge_type, neighbour in adjacency.get(current, []):
            if edge_types and edge_type not in edge_types:
                continue
            if neighbour in visited:  # cycle / already seen -> stop; never loops
                continue
            visited.add(neighbour)
            reached.append((neighbour, depth + 1, edge_type))
            queue.append((neighbour, depth + 1))
    return reached


def subgraph(graph, start, max_depth=1, direction="both", edge_types=None):
    """Return the bounded neighbourhood of ``start`` (for display). Cycle-safe.

    Collects every node within ``max_depth`` of ``start`` (in the requested
    direction(s)) via ``traverse``, then keeps only those nodes and the edges
    whose endpoints both survive. A thin wrapper over :func:`neighborhood`.
    """
    return neighborhood(graph, [start], max_depth, direction, edge_types)


def neighborhood(graph, starts, max_depth=1, direction="both", edge_types=None):
    """Bounded neighbourhood around one or more ``starts`` (multi-seed subgraph).

    Like :func:`subgraph` but seeded by a *set* of start node ids: keeps every
    node within ``max_depth`` hops of ANY start (in the requested direction(s)),
    plus the starts themselves, and the edges whose endpoints both survive.
    ``max_depth=0`` keeps just the starts; ``None`` is unbounded but cycle-safe.
    Accepts a single id or an iterable of ids.
    """
    if isinstance(starts, str):
        starts = [starts]
    keep = set(starts)
    directions = ("out", "in") if direction == "both" else (direction,)
    for s in starts:
        for d in directions:
            for node_id, _, _ in traverse(graph, s, d, max_depth, edge_types):
                keep.add(node_id)
    return {
        "nodes": [n for n in graph["nodes"] if n["id"] in keep],
        "edges": [e for e in graph["edges"] if e["src"] in keep and e["dst"] in keep],
    }


def find_cycles(graph, edge_types=None, limit=100):
    """Diagnostic: report reference cycles (e.g. A calls B calls A).

    Uses an iterative three-colour DFS (no recursion-limit risk). Each returned
    cycle is a node-id list that starts and ends on the same id. At most ``limit``
    cycles are returned. ``edge_types`` (a set) restricts which edges are walked.
    This is purely informational — traversal is bounded regardless of any cycle.
    """
    _, out_adj, _ = index(graph)
    WHITE, GREY, BLACK = 0, 1, 2  # unvisited / on the current DFS path / done
    color: dict[str, int] = {}
    cycles: list[list[str]] = []

    for root in list(out_adj):
        if color.get(root, WHITE) != WHITE:
            continue
        # `stack` holds (node, edge-iterator); `path` is the live DFS path so a
        # back-edge to a GREY node yields the exact cycle by slicing `path`.
        stack = [(root, iter(out_adj.get(root, [])))]
        path = [root]
        color[root] = GREY
        while stack:
            current, edges = stack[-1]
            descended = False
            for edge_type, neighbour in edges:
                if edge_types and edge_type not in edge_types:
                    continue
                neighbour_color = color.get(neighbour, WHITE)
                if neighbour_color == GREY:  # back-edge -> cycle
                    if neighbour in path:
                        cycles.append(path[path.index(neighbour):] + [neighbour])
                        if len(cycles) >= limit:
                            return cycles
                elif neighbour_color == WHITE:
                    color[neighbour] = GREY
                    path.append(neighbour)
                    stack.append((neighbour, iter(out_adj.get(neighbour, []))))
                    descended = True
                    break
            if not descended:  # exhausted this node's edges -> backtrack
                color[current] = BLACK
                stack.pop()
                if path and path[-1] == current:
                    path.pop()
    return cycles
