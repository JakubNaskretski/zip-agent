"""The Librarian — the governed, transactional mutation engine.

This is the ONLY sanctioned path that mutates the KB or the manifest. The agent
never hand-edits files, the manifest, or an index. A change is staged
(``begin`` → ``add_ku`` / ``ingest_ku`` / ``update_ku`` / ``retire_ku`` /
``move_ku``), validated (``preview``), and applied atomically (``commit``) — or
rejected with nothing written.

Invariant map (see docs/ARCHITECTURE.md §3.2):
  I1  single writer        — only commit calls Manifest.save
  I2  computed stats        — Manifest.stats (never stored)
  I3  stable ids            — add refuses to clobber an active id; move re-keys + re-points
  I4  schema valid          — validate_ku on every write
  I5  changelog gate        — begin requires a non-vague rationale; commit always logs
  I6  no orphan links       — derived-from/supersedes/child-of must resolve
  I7  derived rebuilt       — (enforced once index/graph builders land; see rebuild_all)
  I8  raw immutable         — update_ku refuses raw; re-ingest via ingest_ku
  I9  idempotent            — re-ingest of an unchanged content_hash is a no-op
  I11 on-disk session state — SessionState flushed on every staging call
  I12 atomic commit         — file writes via temp+os.replace; ZIP swap in store.pack_zip
  I13 recoverable core      — rebuild_all() regenerates derived from raw+curated
"""
from __future__ import annotations

from datetime import datetime, timezone

from .changelog import Changelog, ChangelogEntry, is_valid_rationale
from .manifest import Manifest
from .schema import KnowledgeUnit, content_hash, validate_ku
from .session import SessionState
from .store import Store

_MUST_RESOLVE_LINKS = {"derived-from", "supersedes", "child-of"}
_UPDATABLE_FIELDS = {
    "title", "entities", "links", "confidence", "status",
    "review_needed", "provenance", "freshness",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_bytes(b) -> bytes:
    if b is None:
        return b""
    return b.encode("utf-8") if isinstance(b, str) else b


class LibrarianError(Exception):
    pass


class Report:
    """Result of preview()/commit(): whether it's valid, what changed, what was
    a no-op, and any rejections."""

    def __init__(self):
        self.ok = True
        self.errors: list = []
        self.changes: list = []        # human-readable change lines
        self.unchanged: list = []      # ids that were no-ops (I9)
        self.committed_generation = None

    def __repr__(self):
        head = "OK" if self.ok else "REJECTED"
        lines = [f"[{head}] {len(self.changes)} change(s), "
                 f"{len(self.unchanged)} unchanged, {len(self.errors)} error(s)"]
        lines += [f"  ~ {c}" for c in self.changes]
        lines += [f"  = unchanged: {u}" for u in self.unchanged]
        lines += [f"  x {e}" for e in self.errors]
        return "\n".join(lines)


class _Op:
    def __init__(self, action, ku=None, body=None, ku_id=None,
                 changes=None, reason=None, new_path=None, new_id=None):
        self.action = action       # ADD | INGEST | UPDATE | RETIRE | MOVE
        self.ku = ku
        self.body = body
        self.ku_id = ku_id or (ku.id if ku else None)
        self.changes = changes or {}
        self.reason = reason
        self.new_path = new_path
        self.new_id = new_id

    def summary(self) -> dict:
        return {"action": self.action, "ku_id": self.ku_id}


class Transaction:
    def __init__(self, lib: "Librarian", author: str, rationale: str):
        if not author or not author.strip():
            raise LibrarianError("author is required to begin a transaction")
        if not is_valid_rationale(rationale):
            raise LibrarianError(
                f"rationale is empty or too vague: {rationale!r} (invariant I5)")
        self.lib = lib
        self.author = author.strip()
        self.rationale = " ".join(str(rationale).split())
        self._ops: list[_Op] = []
        self._plan = None
        # session state on disk (I11)
        self.session = SessionState(
            author=self.author, rationale=self.rationale,
            started_at=_utcnow(), pending=[], path=str(lib.store.session_path),
        )
        self.session.flush()

    # ---- staging ----
    def _stage(self, op: _Op) -> "Transaction":
        self._ops.append(op)
        self.session.pending = [o.summary() for o in self._ops]
        self.session.flush()
        self._plan = None
        return self

    def add_ku(self, ku: KnowledgeUnit, body="") -> "Transaction":
        if not ku.content_hash:
            ku.content_hash = content_hash(body)
        return self._stage(_Op("ADD", ku=ku, body=body))

    def ingest_ku(self, ku: KnowledgeUnit, body="") -> "Transaction":
        """Upsert a derived-or-raw KU (raw/structured/indexes). The digest and the
        index rebuild both use this. Unchanged content is a no-op (I9); changed
        content replaces and flags dependents."""
        if not ku.content_hash:
            ku.content_hash = content_hash(body)
        return self._stage(_Op("INGEST", ku=ku, body=body))

    def update_ku(self, ku_id: str, body=None, **changes) -> "Transaction":
        return self._stage(_Op("UPDATE", ku_id=ku_id, body=body, changes=changes))

    def retire_ku(self, ku_id: str, reason="") -> "Transaction":
        return self._stage(_Op("RETIRE", ku_id=ku_id, reason=reason))

    def move_ku(self, ku_id: str, new_path: str, new_id=None) -> "Transaction":
        return self._stage(_Op("MOVE", ku_id=ku_id, new_path=new_path, new_id=new_id))

    # ---- validation against a projection (never the live manifest) ----
    def preview(self) -> Report:
        rep = Report()
        proj = self.lib.manifest.copy()
        writes: dict = {}
        deletes: set = set()
        rows: list = []
        for op in self._ops:
            try:
                self._apply(op, proj, writes, deletes, rep, rows)
            except LibrarianError as e:
                rep.errors.append(str(e))
        self._check_no_orphans(proj, rep)
        rep.ok = not rep.errors
        self._plan = (proj, writes, deletes, rows)
        return rep

    def commit(self) -> Report:
        rep = self.preview()
        if not rep.ok:
            raise LibrarianError(
                "commit refused:\n" + "\n".join("  - " + e for e in rep.errors))
        proj, writes, deletes, rows = self._plan

        if not rows:
            # everything was a no-op (I9) — do not bump generation or log
            rep.committed_generation = self.lib.manifest.generation
            self.session.clear()
            if self.lib.on_commit:
                self.lib.on_commit(rep)
            return rep

        # apply file writes/deletes (I12: each write is temp+os.replace)
        for rel, data in writes.items():
            self.lib.store.write(rel, data)
        for rel in deletes:
            self.lib.store.delete(rel)

        now = _utcnow()
        proj.generation = self.lib.manifest.generation + 1
        proj.save(self.lib.store.manifest_path, now=now)   # I1: the only writer

        entry = ChangelogEntry(
            generation=proj.generation, timestamp=now,
            author=self.author, rationale=self.rationale,
            changes=[{"action": a, "target": t, "description": d} for (a, t, d) in rows],
        )
        self.lib.changelog.append(entry)
        self.lib.changelog.save(self.lib.store.changelog_path)

        self.lib.manifest = proj      # swap live state only after a successful write
        rep.committed_generation = proj.generation
        self.session.clear()
        if self.lib.on_commit:
            self.lib.on_commit(rep)   # e.g. Session auto-checkpoints the ZIP
        return rep

    # ---- per-op application onto the projection ----
    def _apply(self, op, proj, writes, deletes, rep, rows):
        handler = {
            "ADD": self._do_add, "INGEST": self._do_ingest, "UPDATE": self._do_update,
            "RETIRE": self._do_retire, "MOVE": self._do_move,
        }.get(op.action)
        if handler is None:
            raise LibrarianError(f"unknown op {op.action!r}")
        handler(op, proj, writes, deletes, rep, rows)

    def _do_add(self, op, proj, writes, deletes, rep, rows):
        ku = op.ku
        errs = validate_ku(ku)
        if errs:
            raise LibrarianError(f"invalid KU {ku.id}: " + "; ".join(errs))
        existing = proj.get(ku.id)
        if existing and existing.status == "active":
            raise LibrarianError(
                f"KU {ku.id} already exists (active); use update_ku/ingest_ku (I3)")
        ku.status = "active"
        writes[ku.path] = _as_bytes(op.body)
        proj.put(ku)
        rows.append(("ADDED", ku.id, f"add {ku.kind} at {ku.path}"))
        rep.changes.append(f"ADD {ku.id} ({ku.kind}, {ku.tier})")

    def _do_ingest(self, op, proj, writes, deletes, rep, rows):
        ku = op.ku
        errs = validate_ku(ku)
        if errs:
            raise LibrarianError(f"invalid KU {ku.id}: " + "; ".join(errs))
        if ku.tier not in ("raw", "structured", "indexes"):
            raise LibrarianError(
                f"ingest_ku is for raw/structured/indexes only; {ku.id} is {ku.tier}")
        existing = proj.get(ku.id)
        if (existing and existing.status == "active"
                and existing.content_hash == ku.content_hash):
            rep.unchanged.append(ku.id)          # I9 — true no-op
            return
        ku.status = "active"
        writes[ku.path] = _as_bytes(op.body)
        proj.put(ku)
        if existing:
            rows.append(("UPDATED", ku.id, "re-ingested; content changed"))
            rep.changes.append(f"RE-INGEST {ku.id} (content changed)")
            self._flag_dependents(proj, ku.id, rep, rows)
        else:
            rows.append(("ADDED", ku.id, "ingested new record"))
            rep.changes.append(f"INGEST {ku.id} (new)")

    def _do_update(self, op, proj, writes, deletes, rep, rows):
        existing = proj.get(op.ku_id)
        if not existing:
            raise LibrarianError(f"update_ku: {op.ku_id} not found")
        if existing.tier == "raw":
            raise LibrarianError(
                f"raw KU {op.ku_id} is immutable (I8); re-ingest instead")
        for k, v in (op.changes or {}).items():
            if k not in _UPDATABLE_FIELDS:
                raise LibrarianError(f"update_ku: field {k!r} is not updatable")
            setattr(existing, k, v)
        if op.body is not None:
            existing.content_hash = content_hash(op.body)
            writes[existing.path] = _as_bytes(op.body)
        errs = validate_ku(existing)
        if errs:
            raise LibrarianError(f"invalid KU after update {existing.id}: " + "; ".join(errs))
        proj.put(existing)
        fields = ", ".join((op.changes or {}).keys()) or "(body)"
        rows.append(("UPDATED", existing.id, f"update {fields}"
                     + (" +body" if op.body is not None else "")))
        rep.changes.append(f"UPDATE {existing.id}")

    def _do_retire(self, op, proj, writes, deletes, rep, rows):
        existing = proj.get(op.ku_id)
        if not existing:
            raise LibrarianError(f"retire_ku: {op.ku_id} not found")
        existing.status = "retired"      # never hard-deleted — history preserved
        proj.put(existing)
        rows.append(("RETIRED", existing.id, op.reason or "retired"))
        rep.changes.append(f"RETIRE {existing.id}")
        self._flag_dependents(proj, existing.id, rep, rows)

    def _do_move(self, op, proj, writes, deletes, rep, rows):
        existing = proj.get(op.ku_id)
        if not existing:
            raise LibrarianError(f"move_ku: {op.ku_id} not found")
        old_id = existing.id
        new_id = op.new_id or old_id
        if new_id != old_id:
            clash = proj.get(new_id)
            if clash and clash.status == "active":
                raise LibrarianError(f"move_ku: target id {new_id} already exists")

        body = writes.get(existing.path)
        if body is None:
            body = self.lib.store.read(existing.path) if self.lib.store.exists(existing.path) else b""

        if op.new_path != existing.path:
            deletes.add(existing.path)
            writes[op.new_path] = _as_bytes(body)
            existing.path = op.new_path

        if new_id != old_id:
            for src, ln in proj.inbound_links(old_id):   # I6: re-point inbound links
                ln["to"] = new_id
            proj.remove(old_id)
            existing.id = new_id

        errs = validate_ku(existing)
        if errs:
            raise LibrarianError(f"invalid KU after move {existing.id}: " + "; ".join(errs))
        proj.put(existing)
        rekey = f" re-id {new_id}" if new_id != old_id else ""
        rows.append(("MOVED", old_id, f"-> {existing.path}{rekey}"))
        rep.changes.append(f"MOVE {old_id} -> {existing.path}{rekey}")

    # ---- helpers ----
    def _flag_dependents(self, proj, target_id, rep, rows):
        """Curated KUs that derive-from a changed/retired source → needs-review (§9)."""
        for src, ln in proj.inbound_links(target_id):
            if (ln.get("kind") == "derived-from" and src.tier == "curated"
                    and not src.review_needed):
                src.review_needed = True
                proj.put(src)
                rows.append(("UPDATED", src.id,
                             f"flagged needs-review: source {target_id} changed"))
                rep.changes.append(f"FLAG {src.id} needs-review (depends on {target_id})")

    def _check_no_orphans(self, proj, rep):
        """I6: must-resolve links have to point at an existing KU. (references/
        contradicts may point at graph-node addresses, so they're exempt.)"""
        for ku in proj.all():
            for ln in ku.links:
                to = ln.get("to")
                if to and ln.get("kind") in _MUST_RESOLVE_LINKS and proj.get(to) is None:
                    rep.errors.append(
                        f"orphan link: {ku.id} --{ln['kind']}--> {to} (no such KU)")


class Librarian:
    def __init__(self, store: Store, agent_name="agent"):
        self.store = store
        if store.manifest_path.exists():
            self.manifest = Manifest.load(store.manifest_path)
        else:
            self.manifest = Manifest.new(agent_name=agent_name, now=_utcnow())
        self.changelog = Changelog.load(store.changelog_path)
        # optional post-commit hook (a Session wires this to auto-checkpoint the ZIP)
        self.on_commit = None

    def begin(self, author: str, rationale: str) -> Transaction:
        return Transaction(self, author, rationale)

    def get(self, ku_id):
        return self.manifest.get(ku_id)

    def read_body(self, ku_id):
        ku = self.manifest.get(ku_id)
        return None if ku is None else self.store.read(ku.path)

    def stats(self) -> dict:
        return self.manifest.stats

    def rebuild_all(self) -> dict:
        """Recoverable core (I13): regenerate every derived artifact from
        kb/raw/ + kb/curated/. Stub until the digest/index builders land
        (Phase 3+); kept here so the contract is visible from day one."""
        return {"rebuilt": [], "note": "no derived builders registered yet"}
