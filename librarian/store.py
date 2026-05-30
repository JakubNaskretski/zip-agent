"""The store: the working directory holding the unpacked memory, plus the
atomic ZIP pack/unpack used at session boundaries (invariant I12).

All writes go through a temp file + ``os.replace`` so an interrupted write can
never leave a half-written file — the memory ZIP is the only brain.
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def manifest_path(self):
        return self.root / "manifest.json"

    @property
    def changelog_path(self):
        return self.root / "dev" / "changelog.json"

    @property
    def session_path(self):
        return self.root / "dev" / "session_state.json"

    def abspath(self, rel):
        return self.root / rel

    def exists(self, rel) -> bool:
        return (self.root / rel).exists()

    def read(self, rel) -> bytes:
        return (self.root / rel).read_bytes()

    def write(self, rel, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def delete(self, rel):
        p = self.root / rel
        if p.exists():
            p.unlink()


def unpack_zip(zip_path, dest_dir) -> Store:
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    return Store(dest)


def pack_zip(src_dir, zip_path):
    """Pack a working dir into a ZIP atomically (I12): write to a temp file,
    then ``os.replace`` over the target so an interrupted pack leaves the prior
    ZIP intact. ``.tmp`` scratch files are never included."""
    src = Path(src_dir)
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = zip_path.with_suffix(zip_path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file() and not p.name.endswith(".tmp"):
                zf.write(p, p.relative_to(src).as_posix())
    os.replace(tmp, zip_path)
    return zip_path
