"""Lightweight runtime — the Salesforce vertical slice, end to end.

Proves the three properties the rebuild exists to guarantee:

* **No repack on change** — ingest writes single files into the working folder;
  the base zip is never rewritten. The only whole-zip write is an explicit export.
* **No extractall on boot / navigate** — booting from a zip and navigating reads
  members straight out of the archive; the fresh working folder stays empty.
* **The graph powers navigation** — ingest emits a shard + L0/L1 indexes, and the
  shard resolves names, walks relationships, and yields the verbatim source.

Reuses the synthetic force-app from the digest test, so no org export is needed.
"""
import json

from runtime import boot, layout, navigate
from runtime.ingest import digest_to_tree
from runtime.storage import Workspace

from tests.test_digest_graphbuilder import make_force_app


def _force_app(tmp_path):
    make_force_app(tmp_path)
    return str(tmp_path / "force-app")


# --------------------------------------------------------------------------- #
# ingest writes a tree of single files (no repack), with a shard + indexes
# --------------------------------------------------------------------------- #
def test_digest_to_tree_emits_shard_raw_and_indexes(tmp_path):
    fa = _force_app(tmp_path)
    work = tmp_path / "work"
    ws = Workspace(None, str(work))             # folder-only (no base zip yet)

    summary = digest_to_tree(ws, "salesforce", fa)

    assert summary["nodes"] > 0 and summary["edges"] > 0
    # the shard
    assert ws.exists(layout.graph_shard("salesforce"))
    shard = navigate.load_shard(ws, "salesforce")
    assert any(n["id"] == "object/MeterPoint__c" for n in shard["nodes"])
    # the indexes, regenerated from the shard
    assert ws.exists(layout.INDEX_L0)
    assert ws.exists(layout.index_l1("salesforce"))
    l0 = ws.read_text(layout.INDEX_L0)
    assert "salesforce" in l0 and "object" in l0
    # raw source files, written verbatim under kb/raw/salesforce/
    raws = [p for p in ws.listing(layout.raw_dir("salesforce"))]
    assert any(p.endswith("MeterPointTrigger.trigger") for p in raws)
    assert summary["files_written"] == len(raws)


# --------------------------------------------------------------------------- #
# re-ingest is idempotent — the merge + deterministic shard yield byte-identity
# --------------------------------------------------------------------------- #
def test_reingest_is_byte_identical(tmp_path):
    fa = _force_app(tmp_path)
    ws = Workspace(None, str(tmp_path / "work"))

    digest_to_tree(ws, "salesforce", fa)
    first = ws.read_text(layout.graph_shard("salesforce"))
    digest_to_tree(ws, "salesforce", fa)
    second = ws.read_text(layout.graph_shard("salesforce"))

    assert first == second               # accumulate-not-duplicate (graph merge)


# --------------------------------------------------------------------------- #
# boot from a zip loads ONLY the routing layer; navigation does NOT extractall
# --------------------------------------------------------------------------- #
def test_boot_loads_only_routing_layer_no_extractall(tmp_path):
    fa = _force_app(tmp_path)
    build = Workspace(None, str(tmp_path / "build"))
    digest_to_tree(build, "salesforce", fa)
    # a minimal manifest marking L0 as the on-startup context resource
    build.write_text(layout.MANIFEST, json.dumps({
        "resources": [
            {"path": layout.INDEX_L0, "load_mode": "on_startup", "dest": "context"},
        ]
    }))
    zip_path = build.export(str(tmp_path / "memory.zip"))

    fresh = tmp_path / "session_work"
    session = boot(str(zip_path), str(fresh))

    # context holds the L0 map and nothing heavy
    assert layout.INDEX_L0 in session.context
    assert "object" in session.l0
    assert not any(k.startswith(("graph/", "kb/")) for k in session.context)

    # navigation reads the shard straight from the zip — the working folder stays empty
    g = session.shard("salesforce")
    assert any(n["id"] == "object/MeterPoint__c" for n in g["nodes"])
    extracted = list(fresh.rglob("*"))
    assert extracted == [], f"boot/navigate must not extract the KB, found: {extracted}"


# --------------------------------------------------------------------------- #
# the shard resolves names, walks relationships, and yields the verbatim source
# --------------------------------------------------------------------------- #
def test_navigation_resolve_walk_and_source(tmp_path):
    fa = _force_app(tmp_path)
    ws = Workspace(None, str(tmp_path / "work"))
    digest_to_tree(ws, "salesforce", fa)
    g = navigate.load_shard(ws, "salesforce")

    # resolve an imprecise name to concrete nodes
    hits = navigate.find_nodes(g, "MeterPoint__c")
    assert hits and hits[0]["id"] == "object/MeterPoint__c"

    # the trigger fires on the object — reachable by walking the graph
    nb = navigate.walk(g, "object/MeterPoint__c", depth=2, direction="both")
    ids = {n["id"] for n in nb["nodes"]}
    assert "trigger/MeterPointTrigger" in ids

    # the verbatim source behind the trigger node is retrievable
    tnode = navigate.node(g, "trigger/MeterPointTrigger")
    src = navigate.read_source(ws, "salesforce", tnode)
    assert src is not None and "MeterPointTriggerService" in src
    # excerpt gives a positioned window, not the whole file
    windows = navigate.excerpt(src, "executeAfterInsert")
    assert windows and "executeAfterInsert" in windows[0]


# --------------------------------------------------------------------------- #
# export is the ONLY whole-zip write; the base zip is untouched by ingest
# --------------------------------------------------------------------------- #
def test_export_is_the_only_pack(tmp_path):
    fa = _force_app(tmp_path)
    base = Workspace(None, str(tmp_path / "build"))
    digest_to_tree(base, "salesforce", fa)
    v1 = base.export(str(tmp_path / "memory.zip"))
    mtime_before = v1.stat().st_mtime_ns

    # open the deployed zip as a read-only base + a working overlay, ingest again
    ws = Workspace(str(v1), str(tmp_path / "work2"))
    digest_to_tree(ws, "salesforce", fa)        # writes overlay files only

    assert v1.stat().st_mtime_ns == mtime_before, "ingest must not rewrite the base zip"
    # a new versioned zip is produced only on explicit export
    v2 = ws.export(str(tmp_path / "memory_v2.zip"))
    assert v2.exists() and v2 != v1
