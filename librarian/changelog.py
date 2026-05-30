"""Changelog + the rationale gate.

Invariant I5: every commit appends an entry with a non-empty, non-vague
rationale. The gate lives inside the only write path (the Librarian's commit),
so — unlike the framework we studied — it cannot be bypassed.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Vague descriptions are unacceptable (the studied framework documented this in
# MasterPrompt §10 but never enforced it; here the gate is real).
_VAGUE = {
    "", ".", "..", "...", "update", "updated", "fix", "fixed", "bug fix", "bugfix",
    "various", "various improvements", "misc", "miscellaneous", "changes", "change",
    "stuff", "wip", "tmp", "temp", "improvements", "improvement", "cleanup", "n/a",
}
_MIN_RATIONALE_LEN = 8


def is_valid_rationale(text) -> bool:
    if not text:
        return False
    t = " ".join(str(text).split())
    if t.lower() in _VAGUE:
        return False
    return len(t) >= _MIN_RATIONALE_LEN


@dataclass
class ChangelogEntry:
    generation: int
    timestamp: str
    author: str
    rationale: str
    changes: list = field(default_factory=list)   # [{"action","target","description"}]

    def to_dict(self) -> dict:
        return asdict(self)


class Changelog:
    def __init__(self, entries=None):
        self.entries = list(entries or [])

    @classmethod
    def load(cls, path) -> "Changelog":
        p = Path(path)
        if not p.exists():
            return cls()
        data = json.loads(p.read_text("utf-8"))
        return cls([ChangelogEntry(**e) for e in data.get("entries", [])])

    def append(self, entry: ChangelogEntry):
        self.entries.append(entry)

    def save(self, path):
        payload = {"entries": [e.to_dict() for e in self.entries]}
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, path)
