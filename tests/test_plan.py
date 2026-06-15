"""Durable worklist / plan — the resumable-loop spine that survives a kernel reset.

A plan is a curated KU; the loop marks items done as it goes (each a commit), so
a re-run after a kill resumes from committed state.
"""
import pytest

from librarian import Librarian, Store, plan


def _lib(tmp_path):
    return Librarian(Store(tmp_path / "mem"))


def test_create_lists_pending_and_is_idempotent(tmp_path):
    lib = _lib(tmp_path)
    plan.create_plan(lib, "run1", ["a", "b", "c"], "dev", "create the worklist plan for the loop test")
    assert plan.pending(lib, "run1") == ["a", "b", "c"]
    assert plan.progress(lib, "run1") == {"pending": 3, "done": 0, "skip": 0, "total": 3}
    gen = lib.manifest.generation
    # re-creating must NOT clobber the in-progress plan (idempotent)
    plan.mark(lib, "run1", "a", "done", "dev", "mark item a complete after processing it")
    plan.create_plan(lib, "run1", ["x", "y"], "dev", "attempt to re-create an existing plan")
    assert plan.pending(lib, "run1") == ["b", "c"]          # still the original items, a done
    assert "a" not in plan.pending(lib, "run1")
    assert lib.manifest.generation > gen                    # only the mark bumped it


def test_mark_is_durable_and_resumable(tmp_path):
    lib = _lib(tmp_path)
    plan.create_plan(lib, "r", ["i1", "i2", "i3"], "dev", "seed a resumable plan for the durability test")
    # simulate working the loop, marking as we go
    plan.mark(lib, "r", "i1", "done", "dev", "mark item i1 done after processing it")
    plan.mark(lib, "r", "i2", "skip", "dev", "skip item i2 as not applicable here", note="n/a")
    # a brand-new Librarian over the SAME store = the post-kill / fresh-kernel view
    lib2 = Librarian(Store(tmp_path / "mem"))
    assert plan.pending(lib2, "r") == ["i3"]                 # resumes at the unfinished item
    assert plan.progress(lib2, "r") == {"pending": 1, "done": 1, "skip": 1, "total": 3}
    p = plan.load_plan(lib2, "r")
    skipped = next(it for it in p["items"] if it["id"] == "i2")
    assert skipped["status"] == "skip" and skipped["note"] == "n/a"


def test_mark_unknown_item_appends(tmp_path):
    lib = _lib(tmp_path)
    plan.create_plan(lib, "r", ["a"], "dev", "seed a one-item plan for the append test")
    plan.mark(lib, "r", "late", "done", "dev", "mark a late-discovered item done")
    p = plan.load_plan(lib, "r")
    assert {it["id"] for it in p["items"]} == {"a", "late"}


def test_no_plan_and_bad_status(tmp_path):
    lib = _lib(tmp_path)
    assert plan.load_plan(lib, "missing") is None
    assert plan.pending(lib, "missing") == []
    assert plan.progress(lib, "missing")["total"] == 0
    plan.create_plan(lib, "r", ["a"], "dev", "seed a one-item plan for validation checks")
    with pytest.raises(ValueError):
        plan.mark(lib, "r", "a", "bogus", "dev", "attempt to set an invalid status value")
    with pytest.raises(LookupError):
        plan.mark(lib, "no-such-run", "a", "done", "dev", "mark against a non-existent plan")
