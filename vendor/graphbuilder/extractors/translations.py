"""Translated display labels — attribute donors for nodes defined elsewhere.

Salesforce keeps translations in their own metadata files:

  - ``objectTranslations/<Object>-<locale>/<Object>-<locale>.objectTranslation-meta.xml``
    — the object's translated name (``caseValues``) + record-type labels;
  - ``objectTranslations/<Object>-<locale>/<Field>.fieldTranslation-meta.xml``
    — one file per translated field label;
  - ``translations/<locale>.translation-meta.xml`` — org-wide translations;
    we take custom labels and quick actions.

Each translated NAME becomes a ``label_<locale>`` attr (e.g. ``label_pl``,
``label_en_us``) on the node it annotates. The emitted nodes are marked
``partial: True`` — the core registry merges their attrs into the real node
(defined by objects/labels/quickactions extractors) instead of competing with
it; a translation whose target was not retrieved survives as a ``partial``
node. A multilingual org can then be queried by the words its users actually
use ("which field is called X in Polish?").

Confidentiality: ONLY display-name translations are read (``label`` /
``caseValues/value``). Help text, descriptions, error messages, sections,
report/flow translations are deliberately skipped — names, never content.
"""
from __future__ import annotations

from pathlib import Path

from ..core import node
from ..xmlutil import parse_root


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(el, name: str) -> str:
    for c in el:
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def _iter_local(root, name: str):
    for el in root.iter():
        if _local(el.tag) == name:
            yield el


def _locale_attr(locale: str) -> str:
    return "label_" + locale.lower().replace("-", "_")


def _split_stem(stem: str) -> tuple[str, str]:
    """``MeterPoint__c-pl`` -> (object, locale). API names can't contain ``-``,
    so the last dash splits; locales like ``en_US`` carry no dash themselves."""
    if "-" not in stem:
        return stem, ""
    obj, loc = stem.rsplit("-", 1)
    return obj, loc


def _partial(nid: str, ntype: str, name: str, attr: str, value: str) -> dict:
    return node(nid, ntype, name, partial=True, **{attr: value})


class TranslationExtractor:
    source = "salesforce"

    def handles(self, path: Path) -> bool:
        return path.name.endswith((".objectTranslation-meta.xml",
                                   ".fieldTranslation-meta.xml",
                                   ".translation-meta.xml"))

    def extract(self, path: Path):
        if path.name.endswith(".objectTranslation-meta.xml"):
            return self._object_translation(path)
        if path.name.endswith(".fieldTranslation-meta.xml"):
            return self._field_translation(path)
        return self._org_translation(path)

    # -- objectTranslations/<Object>-<loc>/<Object>-<loc>.objectTranslation-… --
    def _object_translation(self, path: Path):
        obj, loc = _split_stem(path.name[:-len(".objectTranslation-meta.xml")])
        if not obj or not loc:
            return [], []
        attr = _locale_attr(loc)
        root = parse_root(path)
        nodes: list[dict] = []
        # the object's own translated name: singular caseValues entry wins
        value = ""
        for cv in _iter_local(root, "caseValues"):
            v = _child_text(cv, "value")
            if v and (_child_text(cv, "plural") != "true" or not value):
                value = v
                if _child_text(cv, "plural") != "true":
                    break
        if value:
            nodes.append(_partial(f"object/{obj}", "object", obj, attr, value))
        for rt in _iter_local(root, "recordTypes"):
            name, label = _child_text(rt, "name"), _child_text(rt, "label")
            if name and label:
                qual = f"{obj}.{name}"
                nodes.append(_partial(f"recordtype/{qual}", "recordtype",
                                      qual, attr, label))
        return nodes, []

    # -- objectTranslations/<Object>-<loc>/<Field>.fieldTranslation-meta.xml --
    def _field_translation(self, path: Path):
        obj, loc = _split_stem(path.parent.name)
        if not obj or not loc:
            return [], []
        root = parse_root(path)
        name = _child_text(root, "name") \
            or path.name[:-len(".fieldTranslation-meta.xml")]
        label = _child_text(root, "label")
        if not name or not label:
            return [], []
        qual = f"{obj}.{name}"
        return [_partial(f"field/{qual}", "field", qual,
                         _locale_attr(loc), label)], []

    # -- translations/<locale>.translation-meta.xml ---------------------------
    def _org_translation(self, path: Path):
        loc = path.name[:-len(".translation-meta.xml")]
        if not loc:
            return [], []
        attr = _locale_attr(loc)
        root = parse_root(path)
        nodes: list[dict] = []
        for tag, kind in (("customLabels", "label"), ("quickActions", "quickaction")):
            for el in _iter_local(root, tag):
                name, label = _child_text(el, "name"), _child_text(el, "label")
                if name and label:
                    nodes.append(_partial(f"{kind}/{name}", kind, name, attr, label))
        return nodes, []


EXTRACTORS = [TranslationExtractor()]
