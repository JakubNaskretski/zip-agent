"""Workspace — a folder overlaid on a read-only zip base.

This is the lean storage model. The deployed ``memory.zip`` is the read-only
*base*: resources are read straight out of it on demand (``zf.read`` — no
``extractall``, nothing held in memory). Changes are written to a *working
folder* overlay as **single-file writes** — never a whole-archive repack. A read
checks the overlay first, then the base, so a written file shadows the shipped
one. The zip is rebuilt exactly once, on an explicit :meth:`export` — that is the
only place a full archive is packed.

This is what keeps the agent alive in a sandbox that kills long calls: ingesting
a 7k-node org writes one shard file (milliseconds), not a multi-megabyte repack
per change. The zip you upload next session is produced once, when you ask for it.

The class deliberately exposes a tiny, obvious surface (``read_text`` /
``write_text`` / ``exists`` / ``listing`` / ``export``). The agent is free to use
plain ``open(...)`` on paths under :attr:`work` too — there is no blessed API it
must route every write through. ``Workspace`` just makes "read from the zip,
write to the folder, pack only on export" the easy default.
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Iterable, Optional

# matches build_memory.py's pack settings — fast, deterministic enough for transport
_PACK_COMPRESSLEVEL = 6


class Workspace:
    """Read from a zip base, write to a folder overlay, pack only on export.

    Parameters
    ----------
    zip_path:
        The deployed ``memory.zip`` (read-only base). May be ``None`` for a
        folder-only workspace (e.g. building from scratch before the first export).
    work_dir:
        The working folder where changes are written. Created if absent.
    """

    def __init__(self, zip_path: Optional[str], work_dir: str):
        self.zip_path = Path(zip_path) if zip_path else None
        self.work = Path(work_dir)
        self.work.mkdir(parents=True, exist_ok=True)
        self._zf: Optional[zipfile.ZipFile] = None
        self._base_names: Optional[frozenset] = None

    # -- base (zip) access, cached so we open the archive at most once ---------
    def _zip(self) -> Optional[zipfile.ZipFile]:
        if self.zip_path is None:
            return None
        if self._zf is None:
            self._zf = zipfile.ZipFile(self.zip_path)
            self._base_names = frozenset(
                n for n in self._zf.namelist() if not n.endswith("/"))
        return self._zf

    def _base_listing(self) -> frozenset:
        self._zip()
        return self._base_names or frozenset()

    def close(self) -> None:
        if self._zf is not None:
            self._zf.close()
            self._zf = None

    # -- reads (overlay shadows base) ------------------------------------------
    def exists(self, rel: str) -> bool:
        return (self.work / rel).is_file() or rel in self._base_listing()

    def read_bytes(self, rel: str) -> bytes:
        f = self.work / rel
        if f.is_file():
            return f.read_bytes()
        zf = self._zip()
        if zf is not None and rel in self._base_listing():
            return zf.read(rel)
        raise FileNotFoundError(rel)

    def read_text(self, rel: str, encoding: str = "utf-8") -> str:
        return self.read_bytes(rel).decode(encoding)

    # -- writes (single-file, to the overlay — NEVER a repack) -----------------
    def write_bytes(self, rel: str, data: bytes) -> Path:
        p = self.work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)            # one file — no archive rewrite
        return p

    def write_text(self, rel: str, text: str, encoding: str = "utf-8") -> Path:
        return self.write_bytes(rel, text.encode(encoding))

    # -- listing (union of overlay + base) -------------------------------------
    def listing(self, prefix: str = "") -> list:
        """Every resource path under ``prefix`` — overlay and base, de-duped, sorted."""
        names = set(n for n in self._base_listing() if n.startswith(prefix))
        root = self.work
        if root.is_dir():
            for dirpath, _dirs, files in os.walk(root):
                for fn in files:
                    rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
                    if rel.startswith(prefix):
                        names.add(rel)
        return sorted(names)

    def extract_tree(self, prefix: str, dest: Optional[str] = None) -> Path:
        """Materialise a base subtree (e.g. ``graphbuilder/``) onto disk under
        ``dest`` (default: the working folder), so it can be imported or handed
        to the user. Overlay files under ``prefix`` win. Returns the dest root.

        This is the *targeted* extraction the lean model allows — pull the one
        package you need (the parsing engine at ingest, the collectors for the
        handoff), never the whole archive."""
        out = Path(dest) if dest else self.work
        for rel in self.listing(prefix):
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            (out / rel).write_bytes(self.read_bytes(rel))
        return out

    # -- export (the ONE place a whole zip is packed) --------------------------
    def export(self, out_zip: str, extra_skip: Iterable[str] = ()) -> Path:
        """Pack the merged base+overlay into a NEW zip at ``out_zip`` and return it.

        This is the only whole-archive write in the whole runtime. Every base
        member that the overlay did not shadow is copied through; every overlay
        file is written from disk. Output is sorted for a deterministic archive.
        ``out_zip`` must differ from the live ``memory.zip`` — callers version it
        (``memory_v2.zip`` …) so the previous good zip stays as a rollback.
        """
        skip = set(extra_skip)
        out = Path(out_zip)
        out.parent.mkdir(parents=True, exist_ok=True)
        paths = [p for p in self.listing() if p not in skip]
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=_PACK_COMPRESSLEVEL) as zout:
            for rel in paths:
                zout.writestr(rel, self.read_bytes(rel))
        return out
