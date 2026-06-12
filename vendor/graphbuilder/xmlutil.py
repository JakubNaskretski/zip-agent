"""Namespace-agnostic XML helpers shared by the metadata extractors.

Salesforce metadata is XML under a single default namespace
(``http://soap.sforce.com/2006/04/metadata``). Matching by *local name* — the tag
with its ``{namespace}`` prefix stripped — keeps the extractors robust to metadata
exported without the namespace declared, and avoids threading the namespace URI
through every lookup. These two helpers were previously re-defined (near-)verbatim
in ~a dozen extractor modules; they live here now so there is one definition to
reason about.
"""
from __future__ import annotations


def local_name(tag) -> str:
    """Local element name without its ``{namespace}`` prefix
    (``{ns}field`` -> ``field``; ``field`` -> ``field``).

    Returns ``""`` for a non-string tag — an ElementTree comment/PI node's ``.tag``
    is a callable, not a string — so callers walking ``root.iter()`` can match by
    name without tripping over those non-element nodes."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def child(parent, tag: str):
    """First DIRECT child element of ``parent`` whose local name is ``tag``, else
    ``None``. Namespace-agnostic replacement for ``parent.find("ns:tag", NS)``."""
    for c in parent:
        if local_name(c.tag) == tag:
            return c
    return None


def children(parent, tag: str) -> list:
    """All DIRECT child elements of ``parent`` whose local name is ``tag``.
    Namespace-agnostic replacement for ``parent.findall("ns:tag", NS)``."""
    return [c for c in parent if local_name(c.tag) == tag]


def iter_local(root, tag: str) -> list:
    """Every element in ``root``'s subtree (``root`` included) whose local name is
    ``tag``. Namespace-agnostic replacement for ``root.iter("{ns}tag")``."""
    return [el for el in root.iter() if local_name(el.tag) == tag]


def child_text(parent, tag: str) -> str:
    """Stripped text of the first DIRECT child of ``parent`` whose local name is
    ``tag`` (namespace-agnostic), or ``""`` if absent/empty."""
    c = child(parent, tag)
    return (c.text or "").strip() if c is not None else ""


def parse_root(path):
    """Parse a metadata file and return its root element — tolerant of the junk
    real exports carry BEFORE the XML declaration (a UTF-8 BOM, stray
    whitespace/newlines), which makes ``ET.parse`` fail with "XML or text
    declaration not at start of entity". Genuinely malformed XML still raises
    ``ET.ParseError`` so the build records it in ``errors`` instead of hiding it."""
    import xml.etree.ElementTree as ET

    data = path.read_bytes()
    return ET.fromstring(data.lstrip(b"\xef\xbb\xbf\xff\xfe\r\n\t "))
