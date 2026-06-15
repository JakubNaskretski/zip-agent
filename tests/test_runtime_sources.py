"""Lightweight runtime — all five sources port through ``digest_to_tree``.

Reuses each digest test's synthetic fixture builder, so no real export is needed.
Each source must: parse, write its raw files + a graph shard, regenerate the
indexes, and re-ingest byte-identically (the idempotency check now covers the
text-redacted sources — jira/confluence/docs — not just Salesforce). A final
test proves all five coexist in one memory with a single L0 map listing each.
"""
import pytest

from runtime import boot, layout, navigate
from runtime.ingest import digest_to_tree
from runtime.storage import Workspace

import tests.test_digest_graphbuilder as sf_t
import tests.test_digest_mule as mule_t
import tests.test_digest_jira as jira_t
import tests.test_digest_confluence as conf_t
import tests.test_digest_office as office_t


def _sf(root):
    sf_t.make_force_app(root)
    return str(root / "force-app")


def _mule(root):
    return str(mule_t.make_mule_app(root))


def _jira(root):
    return str(jira_t.make_jira_dump(root))


def _conf(root):
    return str(conf_t.make_confluence_dump(root))


def _docs(root):
    return str(office_t.make_docs_dir(root))


# (source key, builder→src path). Each builds under its OWN root so a tree-scanning
# parser (mule scans its whole root) never picks up another source's fixture.
CASES = [
    ("salesforce", _sf),
    ("mule", _mule),
    ("jira", _jira),
    ("confluence", _conf),
    ("docs", _docs),
]


@pytest.mark.parametrize("source,build", CASES, ids=[c[0] for c in CASES])
def test_source_ports_to_tree(tmp_path, source, build):
    src = build(tmp_path / "src")
    ws = Workspace(None, str(tmp_path / "work"))

    summary = digest_to_tree(ws, source, src)

    assert summary["nodes"] > 0, f"{source}: shard has no nodes"
    assert ws.exists(layout.graph_shard(source))
    assert ws.exists(layout.INDEX_L0)
    assert ws.exists(layout.index_l1(source))
    assert source in ws.read_text(layout.INDEX_L0)
    # raw files were written under kb/raw/<source>/
    assert ws.listing(layout.raw_dir(source)), f"{source}: no raw files written"

    # re-ingest is byte-identical (graph merge + deterministic, redacted shard)
    first = ws.read_text(layout.graph_shard(source))
    digest_to_tree(ws, source, src)
    assert ws.read_text(layout.graph_shard(source)) == first, f"{source}: re-ingest drifted"


def test_all_sources_coexist_in_one_memory(tmp_path):
    ws = Workspace(None, str(tmp_path / "work"))
    for source, build in CASES:
        src = build(tmp_path / f"src_{source}")
        digest_to_tree(ws, source, src)

    # every source has its own independent shard
    assert set(navigate.present_sources(ws)) == {s for s, _ in CASES}
    # the L0 map lists all five and is small enough to live in context
    l0 = ws.read_text(layout.INDEX_L0)
    for source, _ in CASES:
        assert source in l0
    assert len(l0) < 4000, "L0 must stay compact (a routing map, not a dump)"

    # shards stay isolated — Salesforce objects never leak into the Mule shard
    sf_ids = {n["id"] for n in navigate.load_shard(ws, "salesforce")["nodes"]}
    mule_ids = {n["id"] for n in navigate.load_shard(ws, "mule")["nodes"]}
    assert sf_ids and mule_ids and sf_ids.isdisjoint(mule_ids)

    # booting over an export loads only L0; the shards stay on demand
    zip_path = ws.export(str(tmp_path / "memory.zip"))
    session = boot(str(zip_path), str(tmp_path / "sess"))
    assert set(session.sources()) == {s for s, _ in CASES}
    assert layout.INDEX_L0 in session.context
