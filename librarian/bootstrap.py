"""Session entry — the one bootstrap (docs/ARCHITECTURE.md §5).

At the start of a session the agent runs this against the retained
``memory.zip``. It unpacks the ZIP into a working dir, makes the in-ZIP
``librarian`` package importable, optionally installs bundled wheels, and
returns a :class:`Session`. The host is an enterprise code-interpreter model;
this code makes no assumption about which one.

The persistence model (corrected): the host retains ``memory.zip`` across
sessions; the user is not in the loop. Within a session the working dir persists
across tool calls. A :class:`Session` with ``autosave=True`` (the default)
re-packs the working dir back into ``memory.zip`` atomically after every commit
that changed anything — so cross-session durability does not depend on the agent
remembering to save.

Runtime entry (paste into the sandbox at session start)::

    import sys, zipfile
    work = "/mnt/data/memory_work"
    with zipfile.ZipFile("/mnt/data/memory.zip") as z:
        z.extractall(work)
    sys.path.insert(0, work)
    from librarian.bootstrap import boot
    session = boot("/mnt/data/memory.zip", work_dir=work)
    # ... session.librarian.begin(author, rationale)... .commit()  (auto-checkpoints)
"""
from __future__ import annotations

import glob
import subprocess
import sys
from pathlib import Path

from .librarian import Librarian
from .store import Store, pack_zip, unpack_zip


class Session:
    """A live agent session bound to a working dir and (optionally) a retained ZIP."""

    def __init__(self, store: Store, librarian: Librarian, memory_zip=None, autosave=True):
        self.store = store
        self.librarian = librarian
        self.memory_zip = Path(memory_zip) if memory_zip else None
        self.autosave = autosave
        self.last_checkpoint_generation = None
        if autosave and self.memory_zip is not None:
            self.librarian.on_commit = self._after_commit

    # ---- convenience passthroughs ----
    def begin(self, author, rationale):
        return self.librarian.begin(author, rationale)

    def get(self, ku_id):
        return self.librarian.get(ku_id)

    def stats(self):
        return self.librarian.stats()

    # ---- persistence ----
    def _after_commit(self, rep):
        # only re-pack when the commit actually changed memory (skip I9 no-ops)
        if rep.changes:
            self.checkpoint()

    def checkpoint(self, reason=None):
        """Atomically re-pack the working dir into the retained memory ZIP (I12)."""
        if self.memory_zip is None:
            return None
        path = pack_zip(self.store.root, self.memory_zip)
        self.last_checkpoint_generation = self.librarian.manifest.generation
        return path

    def export(self, dest):
        """Hand the user a copy of the memory ZIP on request (not required for persistence)."""
        return pack_zip(self.store.root, dest)


def _install_wheelhouse(work_dir) -> dict:
    """Best-effort install of bundled offline wheels. No-op when absent."""
    wh = Path(work_dir) / "reference" / "wheelhouse"
    if not wh.is_dir():
        return {"installed": False, "reason": "no wheelhouse bundled"}
    wheels = sorted(glob.glob(str(wh / "*.whl")))
    if not wheels:
        return {"installed": False, "reason": "wheelhouse empty"}
    # --upgrade matters: sandboxes often PREINSTALL an older tree-sitter, and
    # without it pip leaves the old (property-style-API) binding in place — the
    # engine's probe then correctly refuses it and Apex stays on regex. Retry
    # on the user site for hosts whose system site-packages is read-only.
    # install by NAME, not by wheel file: explicit files pin exact versions,
    # so a wheelhouse holding two versions of one package would be unresolvable
    # — by name, pip just picks the best wheel available in the dir
    names = sorted({Path(w).name.split("-")[0].replace("_", "-") for w in wheels})
    cmd = [sys.executable, "-m", "pip", "install", "--no-index", "--upgrade",
           "--find-links", str(wh), *names]
    last = None
    for extra in ((), ("--user",)):
        try:
            last = subprocess.run([*cmd, *extra], capture_output=True, text=True)
        except Exception as e:   # pragma: no cover - environment dependent
            return {"installed": False, "reason": str(e)}
        if last.returncode == 0:
            out = {"installed": True, "count": len(wheels)}
            if extra:
                out["user_site"] = True
            return out
    return {"installed": False,   # pip's own words, so the boot report says WHY
            "reason": ((last.stderr or last.stdout or "").strip())[-400:]}


def boot(memory_zip, work_dir=None, install_wheelhouse=True, autosave=True) -> Session:
    """Open the retained memory ZIP and return a ready :class:`Session`.

    If ``memory_zip`` does not exist yet (a brand-new agent), an empty working
    dir is created and the first :meth:`Session.checkpoint` writes the ZIP.
    If ``work_dir`` is already unpacked, it is reused rather than re-extracted.
    """
    memory_zip = Path(memory_zip)
    if work_dir is None:
        work_dir = memory_zip.parent / (memory_zip.stem + "_work")
    work_dir = Path(work_dir)

    already_unpacked = (work_dir / "manifest.json").exists() or (work_dir / "librarian").is_dir()
    if memory_zip.exists() and not already_unpacked:
        store = unpack_zip(memory_zip, work_dir)
    else:
        store = Store(work_dir)

    # make the in-ZIP librarian package importable at runtime (no-op in dev/tests
    # where the package isn't shipped inside the memory ZIP)
    if (work_dir / "librarian").is_dir() and str(work_dir) not in sys.path:
        sys.path.insert(0, str(work_dir))

    if install_wheelhouse:
        _install_wheelhouse(work_dir)

    return Session(store, Librarian(store), memory_zip=memory_zip, autosave=autosave)
