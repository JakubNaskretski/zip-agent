"""The manifest — the single source of truth for what's in memory.

Invariants enforced here:
  I1  single writer    — :meth:`Manifest.save` is the only serializer of
                         ``manifest.json``; only the Librarian calls it.
  I2  computed stats   — :attr:`Manifest.stats` is derived on read, never stored.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from .schema import KnowledgeUnit

MANIFEST_VERSION = "1.0"


class Manifest:
    def __init__(self, agent_name="agent", manifest_version=MANIFEST_VERSION,
                 generation=0, created="", updated="", kus=None):
        self.agent_name = agent_name
        self.manifest_version = manifest_version
        self.generation = generation
        self.created = created
        self.updated = updated
        self._kus: dict[str, KnowledgeUnit] = dict(kus or {})

    # ---- construction / IO ----
    @classmethod
    def new(cls, agent_name="agent", now=""):
        return cls(agent_name=agent_name, generation=0, created=now, updated=now)

    @classmethod
    def load(cls, path) -> "Manifest":
        data = json.loads(Path(path).read_text("utf-8"))
        kus = {}
        for d in data.get("resources", []):
            ku = KnowledgeUnit.from_dict(d)
            kus[ku.id] = ku
        return cls(
            agent_name=data.get("agent_name", "agent"),
            manifest_version=data.get("manifest_version", MANIFEST_VERSION),
            generation=data.get("generation", 0),
            created=data.get("created", ""),
            updated=data.get("updated", ""),
            kus=kus,
        )

    def save(self, path, now=""):
        """The ONLY writer of manifest.json (I1). Atomic at file level: write a
        temp file then ``os.replace`` over the target (same mechanism as I12)."""
        self.updated = now or self.updated
        payload = {
            "manifest_version": self.manifest_version,
            "agent_name": self.agent_name,
            "generation": self.generation,
            "created": self.created,
            "updated": self.updated,
            # stats is intentionally NOT persisted (I2) — see the `stats` property.
            "resources": [k.to_dict() for k in self._kus.values()],
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, path)

    # ---- computed view (I2) ----
    @property
    def stats(self) -> dict:
        def tally(attr):
            out: dict = {}
            for k in self._kus.values():
                key = getattr(k, attr)
                out[key] = out.get(key, 0) + 1
            return out

        return {
            "total": len(self._kus),
            "generation": self.generation,
            "by_tier": tally("tier"),
            "by_source": tally("source"),
            "by_kind": tally("kind"),
            "by_status": tally("status"),
        }

    # ---- data access (mutation flows through the Librarian) ----
    def get(self, ku_id):
        return self._kus.get(ku_id)

    def all(self):
        return list(self._kus.values())

    @property
    def entries(self):
        """Read-only alias of :meth:`all`. :class:`~librarian.changelog.Changelog`
        exposes its rows as ``.entries`` and deployed hosts generalize that
        naming to the manifest (seen in the field as ``AttributeError:
        'Manifest' object has no attribute 'entries'``) — so the manifest
        answers to the same name. Returns a fresh list; mutation still flows
        through the Librarian only."""
        return self.all()

    def put(self, ku: KnowledgeUnit):
        self._kus[ku.id] = ku

    def remove(self, ku_id):
        self._kus.pop(ku_id, None)

    def inbound_links(self, ku_id):
        """Every (source_ku, link) whose ``link['to'] == ku_id``. Used for the
        no-orphan-links invariant (I6) and staleness flagging (§9)."""
        out = []
        for k in self._kus.values():
            for ln in k.links:
                if ln.get("to") == ku_id:
                    out.append((k, ln))
        return out

    def copy(self) -> "Manifest":
        """Deep copy — preview validates against a projection, never the live one."""
        return copy.deepcopy(self)
