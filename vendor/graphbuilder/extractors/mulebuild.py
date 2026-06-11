"""Mule build-metadata extractor (``pom.xml`` + ``mule-artifact.json``).

Both files sit at a Mule app's root, next to ``src/main/mule`` — and that
sibling directory is the gate: ``extract`` emits nothing for a pom/descriptor
that isn't a Mule app's (``handles`` stays a cheap name check; no other
extractor claims these file names).

  - ``pom.xml`` -> the app-singleton ``muleartifactdescriptor/app`` node (label =
    the ``artifactId``) plus one ``pomdependency/<groupId>:<artifactId>`` node and
    an app ``dependson`` edge per ``<dependency>``. The ``version`` attr keeps the
    raw text (often a ``${property}`` — structural, so kept verbatim).
  - ``mule-artifact.json`` -> the same app node (attrs ``minMuleVersion``, and
    ``name`` when the descriptor carries one), plus — for every entry in
    ``secureProperties`` — a ``propertykey`` node flagged ``secure=True`` and a
    ``securedby`` edge back to the app. Property VALUES never appear in either
    file; this captures which keys are secret-bearing WITHOUT touching secrets.

The descriptor file sorts before ``pom.xml`` and both sort before ``src/…`` in
the build's path walk, so when both exist the descriptor's richer app node wins
the registry's first-emission rule.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import node, raw_edge
from ..mulesoft import local_name
from .mule import APP_ID


def _is_mule_app_root(path: Path) -> bool:
    return (path.parent / "src" / "main" / "mule").is_dir() or \
           (path.parent / "src" / "main" / "app").is_dir()


def _text_of(el, tag: str) -> str:
    """Text of the first DIRECT child with local tag ``tag`` (namespace-tolerant)."""
    for child in el:
        if local_name(child.tag) == tag:
            return (child.text or "").strip()
    return ""


class MuleBuildExtractor:
    source = "mule"

    def handles(self, path: Path) -> bool:
        return path.name in ("pom.xml", "mule-artifact.json")

    def extract(self, path: Path):
        if not _is_mule_app_root(path):
            return [], []
        if path.name == "pom.xml":
            return self._extract_pom(path)
        return self._extract_descriptor(path)

    def _extract_pom(self, path: Path):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            return [], []
        if local_name(root.tag) != "project":
            return [], []
        artifact_id = _text_of(root, "artifactId")
        app = node(APP_ID, "muleartifactdescriptor", artifact_id or "app",
                   file=path.name)
        nodes, edges = [app], []
        deps = next((c for c in root if local_name(c.tag) == "dependencies"), None)
        for dep in deps if deps is not None else []:
            if local_name(dep.tag) != "dependency":
                continue
            group = _text_of(dep, "groupId")
            artifact = _text_of(dep, "artifactId")
            if not artifact:
                continue
            name = f"{group}:{artifact}" if group else artifact
            attrs = {}
            if _text_of(dep, "version"):
                attrs["version"] = _text_of(dep, "version")
            if _text_of(dep, "classifier"):
                attrs["classifier"] = _text_of(dep, "classifier")
            nodes.append(node(f"pomdependency/{name}", "pomdependency", name, **attrs))
            edges.append(raw_edge(APP_ID, "dependson", "pomdependency", name))
        return nodes, edges

    def _extract_descriptor(self, path: Path):
        try:
            doc = json.loads(path.read_text("utf-8", errors="replace"))
        except (json.JSONDecodeError, ValueError):
            return [], []
        if not isinstance(doc, dict):
            return [], []
        attrs = {"file": path.name}
        if isinstance(doc.get("minMuleVersion"), str):
            attrs["minMuleVersion"] = doc["minMuleVersion"]
        label = doc.get("name") if isinstance(doc.get("name"), str) else ""
        nodes = [node(APP_ID, "muleartifactdescriptor", label or "app", **attrs)]
        edges = []
        secure = doc.get("secureProperties")
        for key in secure if isinstance(secure, list) else []:
            if not isinstance(key, str) or not key:
                continue
            nodes.append(node(f"propertykey/{key}", "propertykey", key, secure=True))
            edges.append(raw_edge(f"propertykey/{key}", "securedby",
                                  "muleartifactdescriptor", "app"))
        return nodes, edges


EXTRACTORS = [MuleBuildExtractor()]
