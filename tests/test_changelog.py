from librarian import Changelog, ChangelogEntry, is_valid_rationale


def test_rationale_gate_rejects_empty_and_vague():
    for bad in ["", "   ", "fix", "update", "WIP", "various improvements", "bug fix", "."]:
        assert not is_valid_rationale(bad), bad


def test_rationale_gate_accepts_real_reasons():
    assert is_valid_rationale("ingest April Confluence export")
    assert is_valid_rationale("merge duplicate metering glossary entries")


def test_changelog_roundtrip(tmp_path):
    cl = Changelog()
    cl.append(ChangelogEntry(
        generation=1, timestamp="2026-05-29T00:00:00Z", author="dev",
        rationale="ingest first batch",
        changes=[{"action": "ADDED", "target": "jira:PROJ-1", "description": "add"}],
    ))
    path = tmp_path / "changelog.json"
    cl.save(path)
    back = Changelog.load(path)
    assert len(back.entries) == 1
    assert back.entries[0].author == "dev"
    assert back.entries[0].changes[0]["target"] == "jira:PROJ-1"
