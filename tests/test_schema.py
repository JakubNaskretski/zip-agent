from librarian import KnowledgeUnit, validate_ku, content_hash
from factories import jira_ku, curated_ku


def test_valid_kus_pass():
    assert validate_ku(jira_ku()) == []
    assert validate_ku(curated_ku()) == []


def test_bad_id_namespace():
    ku = jira_ku(id="bogus:PROJ-1")
    errs = validate_ku(ku)
    assert any("namespace" in e for e in errs)


def test_id_namespace_must_match_source():
    ku = jira_ku(id="confluence:PROJ-1")  # source still 'jira'
    assert any("must match source" in e for e in validate_ku(ku))


def test_curated_id_requires_agent_source():
    ku = curated_ku(source="jira")
    assert any("source 'agent'" in e for e in validate_ku(ku))


def test_bad_kind_tier_confidence_status():
    ku = jira_ku(kind="nope", tier="nope", confidence="nope", status="nope")
    errs = " ".join(validate_ku(ku))
    assert "kind" in errs and "tier" in errs and "confidence" in errs and "status" in errs


def test_path_lane_enforced():
    ku = jira_ku(path="kb/curated/jira/PROJ-1.json")  # raw must be under kb/raw/
    assert any("must start with 'kb/raw/'" in e for e in validate_ku(ku))


def test_bad_link_shape_and_kind():
    assert any("must be a dict" in e for e in validate_ku(jira_ku(links=["x"])))
    assert any("link kind" in e for e in validate_ku(
        jira_ku(links=[{"kind": "wat", "to": "mule:x"}])))


def test_content_hash_stable_and_changes():
    assert content_hash("a") == content_hash("a")
    assert content_hash("a") != content_hash("b")
    assert content_hash(b"a") == content_hash("a")
