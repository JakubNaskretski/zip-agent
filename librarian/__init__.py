"""Librarian — the governed, transactional knowledge-base engine.

The Librarian is the ONLY sanctioned path that mutates the memory ZIP's
knowledge base or manifest. Every change is staged, validated against all
invariants, and committed atomically — or rejected. See
``plan/03_ARCHITECTURE.md`` §3 for the design and the invariant catalogue (I1–I13).
"""

from .schema import KnowledgeUnit, validate_ku, content_hash
from .manifest import Manifest, MANIFEST_VERSION
from .changelog import Changelog, ChangelogEntry, is_valid_rationale
from .session import SessionState
from .store import Store, pack_zip, unpack_zip
from .librarian import Librarian, Transaction, Report, LibrarianError
from .bootstrap import boot, Session
from .index import rebuild_indexes, build_index, INDEX_ID
from . import retrieve, index

__all__ = [
    "KnowledgeUnit", "validate_ku", "content_hash",
    "Manifest", "MANIFEST_VERSION",
    "Changelog", "ChangelogEntry", "is_valid_rationale",
    "SessionState",
    "Store", "pack_zip", "unpack_zip",
    "Librarian", "Transaction", "Report", "LibrarianError",
    "boot", "Session",
    "rebuild_indexes", "build_index", "INDEX_ID", "retrieve", "index",
]
