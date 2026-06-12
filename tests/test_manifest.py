import json

import pytest

from librarian import Manifest
from factories import jira_ku, curated_ku


def test_new_manifest_is_empty():
    m = Manifest.new()
    assert m.stats["total"] == 0
    assert m.generation == 0


def test_stats_computed_by_dimension():
    m = Manifest.new()
    m.put(jira_ku(1))
    m.put(jira_ku(2))
    m.put(curated_ku())
    s = m.stats
    assert s["total"] == 3
    assert s["by_tier"]["raw"] == 2
    assert s["by_tier"]["curated"] == 1
    assert s["by_source"]["jira"] == 2


def test_save_load_roundtrip_and_stats_not_persisted(tmp_path):
    m = Manifest.new(agent_name="t")
    m.put(jira_ku(1))
    m.generation = 5
    path = tmp_path / "manifest.json"
    m.save(path, now="2026-05-29T00:00:00Z")

    raw = json.loads(path.read_text())
    assert "stats" not in raw            # I2: computed, never stored
    assert raw["generation"] == 5

    back = Manifest.load(path)
    assert back.generation == 5
    assert back.get("jira:PROJ-1").title == "Issue 1"
    assert back.stats["total"] == 1


def test_entries_is_a_read_only_alias_of_all():
    """Hosts generalize Changelog's `.entries` naming to the manifest — the
    alias keeps that from blowing up with an AttributeError."""
    m = Manifest.new()
    m.put(jira_ku(1))
    m.put(curated_ku())
    assert m.entries == m.all()
    assert {k.id for k in m.entries} == {"jira:PROJ-1", "curated:mappings/meter-map"}

    m.entries.append("junk")             # a fresh list each time — no back door
    assert len(m.entries) == 2
    with pytest.raises(AttributeError):  # and no setter
        m.entries = []


def test_inbound_links():
    m = Manifest.new()
    m.put(jira_ku(1))
    m.put(curated_ku(derived_from="jira:PROJ-1"))
    inbound = m.inbound_links("jira:PROJ-1")
    assert len(inbound) == 1
    assert inbound[0][0].id == "curated:mappings/meter-map"
