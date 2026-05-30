"""On-disk session state — invariant I11.

The framework we studied kept author/rationale/pending-changes in Python module
globals, which evaporate on a sandbox restart (analysis finding #3, fatal here).
This lives in ``dev/session_state.json`` and is flushed on every change.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class SessionState:
    author: str = ""
    rationale: str = ""
    started_at: str = ""
    pending: list = field(default_factory=list)
    path: str = ""    # where to flush; not serialized into the payload

    def flush(self):
        if not self.path:
            return
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in asdict(self).items() if k != "path"}
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, p)

    @classmethod
    def load(cls, path) -> "SessionState":
        p = Path(path)
        if not p.exists():
            return cls(path=str(path))
        d = json.loads(p.read_text("utf-8"))
        d["path"] = str(path)
        return cls(**d)

    def clear(self):
        self.author = ""
        self.rationale = ""
        self.started_at = ""
        self.pending = []
        self.flush()
