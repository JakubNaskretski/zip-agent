"""Export a Salesforce graph as an Obsidian vault (markdown + [[wikilinks]]).

Ingests a force-app with the Librarian, then writes one note per graph node with
its relationships as wikilinks, organised in type folders — so Obsidian's graph
view renders the metadata map and each note is a readable hub.

    python scripts/export_obsidian.py <force-app-dir> <vault-out-dir>

NOTE: the output contains real org names — keep the vault local (it's gitignored).
The script itself is generic/anonymous.
"""
from __future__ import annotations

import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from librarian import Librarian, Store
from librarian.digest import graphbuilder as sf

TYPE_FOLDER = {
    "object": "Objects", "apexclass": "Apex", "trigger": "Triggers", "flow": "Flows",
    "lwc": "LWC", "flexipage": "Pages", "permissionset": "Security",
    "profile": "Security", "permsetgroup": "Security", "omniscript": "OmniStudio",
    "integrationprocedure": "OmniStudio", "datamapper": "OmniStudio", "flexcard": "OmniStudio",
}
EDGE_OUT = {
    "calls": "Calls", "on": "Trigger on", "touches": "Touches", "references": "References",
    "grants": "Grants access to", "contains": "Contains", "page-for": "Page for",
    "embeds": "Embeds", "uses": "Uses", "uses-component": "Uses component", "maps": "Maps",
}
EDGE_IN = {
    "calls": "Called by", "on": "Has trigger", "touches": "Touched by",
    "references": "Referenced by", "grants": "Access granted by", "contains": "Member of",
    "page-for": "Has page", "embeds": "Embedded in", "uses": "Used by",
    "uses-component": "Used by component", "maps": "Mapped by",
}


def _name(nid):
    return nid.split("/", 1)[1] if "/" in nid else nid


def _display(nid):
    """Field nodes resolve to their parent object (object-level graph)."""
    if nid.startswith("field/"):
        return "object/" + _name(nid).split(".")[0]
    return nid


def _fname(title):
    return re.sub(r'[\\/:*?"<>|]', "_", title)


def export(force_app, out_dir):
    work = tempfile.mkdtemp()
    lib = Librarian(Store(work))
    sf.ingest_salesforce(lib, force_app, "dev", "export to obsidian")
    g = sf.load_graph(lib)
    nodes = {n["id"]: n for n in g["nodes"]}

    # unique titles (append type only on collision)
    types_by_name = defaultdict(set)
    for nid, n in nodes.items():
        if n["type"] != "field":
            types_by_name[_name(nid)].add(n["type"])

    def title(nid):
        base = _name(nid)
        return base if len(types_by_name[base]) <= 1 else f"{base} ({nodes[nid]['type']})"

    # fields + lookups (object-level)
    fields = defaultdict(list)
    lookups, looked_up = defaultdict(set), defaultdict(set)
    for e in g["edges"]:
        if e["type"] == "field_of":
            fn = nodes.get(e["src"])          # graph-builder: field --field_of--> object
            if fn:
                fields[e["dst"]].append((_name(fn["id"]).split(".", 1)[-1],
                                         fn.get("field_type", fn.get("ftype", ""))))
        elif e["type"] == "lookup":
            s = _display(e["src"])
            lookups[s].add(e["dst"]); looked_up[e["dst"]].add(s)

    # adjacency (skip field_of/lookup; collapse field endpoints to objects)
    out_adj = defaultdict(lambda: defaultdict(set))
    in_adj = defaultdict(lambda: defaultdict(set))
    for e in g["edges"]:
        if e["type"] in ("field_of", "lookup"):
            continue
        s, d = _display(e["src"]), _display(e["dst"])
        if s != d:
            out_adj[s][e["type"]].add(d); in_adj[d][e["type"]].add(s)

    out = Path(out_dir)
    counts = defaultdict(int)
    degree = {}
    for nid, n in nodes.items():
        if n["type"] == "field":
            continue
        counts[n["type"]] += 1
        t = title(nid)
        L = ["---", f"tags: [sf/{n['type']}]", f"type: {n['type']}"]
        if n.get("external"):
            L.append("external: true")
        L += ["---", f"# {t}", "",
              f"*{n['type']}*" + (" · external (referenced, not retrieved)" if n.get("external") else "")]
        body_links = 0

        if n["type"] == "object" and fields.get(nid):
            L += ["", "## Fields"] + [f"- {fn}" + (f" *({ft})*" if ft else "") for fn, ft in sorted(fields[nid])]
        for label, targets in (("Lookups to", lookups.get(nid)), ("Looked up by", looked_up.get(nid))):
            if targets:
                L += ["", f"## {label}"] + [f"- [[{title(x)}]]" for x in sorted(targets, key=title)]
                body_links += len(targets)
        for et, dsts in sorted(out_adj[nid].items()):
            L += ["", f"## {EDGE_OUT.get(et, et)}"] + [f"- [[{title(x)}]]" for x in sorted(dsts, key=title)]
            body_links += len(dsts)
        for et, srcs in sorted(in_adj[nid].items()):
            L += ["", f"## {EDGE_IN.get(et, et)}"] + [f"- [[{title(x)}]]" for x in sorted(srcs, key=title)]
            body_links += len(srcs)

        degree[nid] = body_links
        p = out / TYPE_FOLDER.get(n["type"], "Other") / (_fname(t) + ".md")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(L) + "\n", "utf-8")

    # dashboard / map-of-content
    idx = ["# Salesforce Org Map", "", "Auto-generated from the metadata graph. Open the graph view (⌘/Ctrl-G).", "",
           "## Counts", ""]
    idx += [f"- **{counts[t]}** {t}" for t in sorted(counts)]
    top = sorted(degree, key=lambda k: degree[k], reverse=True)[:15]
    idx += ["", "## Most-connected nodes", ""]
    idx += [f"- [[{title(nid)}]] — {degree[nid]} links ({nodes[nid]['type']})" for nid in top]
    (out / "_Org Map.md").write_text("\n".join(idx) + "\n", "utf-8")

    return dict(counts), sum(counts.values())


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: export_obsidian.py <force-app-dir> <vault-out-dir>")
    counts, total = export(sys.argv[1], sys.argv[2])
    print(f"wrote {total} notes to {sys.argv[2]}")
    for t, c in sorted(counts.items()):
        print(f"  {c:4} {t}")
