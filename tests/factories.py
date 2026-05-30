"""Small KU factories for tests."""
from librarian import KnowledgeUnit


def jira_ku(num=1, body="body", **over):
    d = dict(
        id=f"jira:PROJ-{num}", kind="source-record", tier="raw", source="jira",
        path=f"kb/raw/jira/PROJ-{num}.json", title=f"Issue {num}",
        entities=["MeterPointService"], confidence="VERIFIED",
    )
    d.update(over)
    return KnowledgeUnit(**d)


def mule_ku(name="meterPointSync", body="<flow/>", **over):
    d = dict(
        id=f"mule:{name}", kind="source-record", tier="raw", source="mule",
        path=f"kb/raw/mule/{name}.xml", title=name,
    )
    d.update(over)
    return KnowledgeUnit(**d)


def curated_ku(slug="meter-map", derived_from=None, **over):
    links = [{"kind": "derived-from", "to": derived_from}] if derived_from else []
    d = dict(
        id=f"curated:mappings/{slug}", kind="curated-note", tier="curated",
        source="agent", path=f"kb/curated/mappings/{slug}.md", title=slug, links=links,
    )
    d.update(over)
    return KnowledgeUnit(**d)
