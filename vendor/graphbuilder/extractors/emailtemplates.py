"""Extracts email templates (`*.email-meta.xml`).

A template becomes `emailtemplate/<Name>` (Name from the filename stem) with
structural attrs `folder` (the containing email folder, from the path) and
`template_type` (the declared `<type>`: text | html | custom | visualforce).
Edges: `references` -> object for the bound `<relatedEntityType>` (or
`<relatedEntity>` / `<entityType>`); `reads` -> field for `Object.Field` merge
tokens found only in safe structural metadata fields.

Names and structure only. The subject and body are never read: content-bearing
tags are blocklisted, and merge tokens are scanned only in a whitelist of
structural fields, so message text can never leak even as a merge name. Parsing
is namespace-agnostic and skips malformed files.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..xmlutil import local_name as _local

_SUFFIX = ".email-meta.xml"

# Tags whose text is message content or other free text — never read, emitted,
# or scanned for merge tokens. Matched by lower-cased local name.
_CONFIDENTIAL_TAGS = frozenset({
    "subject", "htmlvalue", "textvalue", "text", "content", "body", "textonly",
    "value", "description", "letterhead",
})

# Structural tags whose text names the bound object/entity for the template.
_ENTITY_TAGS = ("relatedentitytype", "relatedentity", "entitytype")

# Structural tags safe to scan for `Object.Field` merge tokens. The body/subject
# are never scanned — only metadata fields that name structure.
_MERGE_SCAN_TAGS = frozenset({
    "relatedentitytype", "relatedentity", "entitytype",
    "field", "fieldname", "mergefield", "sortfield",
})

# A strict `Object.Field` merge token: two dotted API-name segments, optionally
# wrapped in `{! ... }`. Both segments are required; longer dotted chains are
# rejected so arbitrary dotted text isn't captured.
_MERGE = re.compile(r"\{?!?\s*([A-Za-z]\w*)\.([A-Za-z]\w*)\s*\}?")


def _first_text(root, tag: str) -> str:
    """Stripped text of the first element whose local name is `tag`, else ""."""
    lt = tag.lower()
    for el in root.iter():
        if _local(el.tag).lower() == lt:
            return (el.text or "").strip()
    return ""


def _folder_from_path(path: Path) -> str:
    """The email folder is the segment directly under an `email/` ancestor in the
    standard `email/<Folder>/<T>.email-meta.xml` layout. Returns "" when the
    template is not nested under such a folder; only the `email/` convention
    names a real folder, so an arbitrary parent dir is never used."""
    parts = path.parts
    try:
        idx = max(i for i, p in enumerate(parts) if p.lower() == "email")
    except ValueError:
        return ""
    # need a segment under `email/` that is itself a directory (file comes after).
    if idx + 1 < len(parts) - 1:
        return parts[idx + 1]
    return ""


class EmailTemplateExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(_SUFFIX)

    def extract(self, path: Path):
        name = path.name[: -len(_SUFFIX)] or path.stem
        eid = f"emailtemplate/{name}"

        attrs: dict = {}
        folder = ""
        try:
            folder = _folder_from_path(path)
        except Exception:
            folder = ""
        if folder:
            attrs["folder"] = folder

        nodes = [node(eid, "emailtemplate", name, **attrs)]
        edges: list[dict] = []

        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            return nodes, edges
        if root is None:
            return nodes, edges

        # --- template_type: the declared <type> (structural enum, not content) --
        try:
            template_type = _first_text(root, "type")
        except Exception:
            template_type = ""
        if template_type:
            nodes[0]["template_type"] = template_type

        # --- references -> object: the bound entity -----------------------------
        on_object = ""
        for tag in _ENTITY_TAGS:
            try:
                on_object = _first_text(root, tag)
            except Exception:
                on_object = ""
            if on_object:
                break
        if on_object:
            edges.append(raw_edge(eid, "references", "object", on_object))

        # --- reads -> field: merge tokens in SAFE structural fields only --------
        seen_fields: set[str] = set()
        try:
            iterator = list(root.iter())
        except Exception:
            iterator = []
        for el in iterator:
            ln = _local(el.tag).lower()
            # Hard skip of any content/free-text element — never read its text.
            if ln in _CONFIDENTIAL_TAGS:
                continue
            if ln not in _MERGE_SCAN_TAGS:
                continue
            txt = el.text or ""
            if not txt.strip():
                continue
            for m in _MERGE.finditer(txt):
                obj, fld = m.group(1), m.group(2)
                if not obj or not fld:
                    continue
                qualified = f"{obj}.{fld}"
                if qualified in seen_fields:
                    continue
                seen_fields.add(qualified)
                edges.append(raw_edge(eid, "reads", "field", qualified))

        return nodes, edges


EXTRACTORS = [EmailTemplateExtractor()]
