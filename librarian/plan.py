"""Durable worklist / plan — the spine of crash-resilient, resumable work.

The sandbox kills the Python process on a timeout or memory pressure and every
in-memory variable dies with it. The durable state is on disk (the KB + manifest
in ``memory.zip`` / the unpacked work dir), so the way to survive a kernel reset
is to keep your *plan of work* on disk too and drive a loop off it:

    from librarian import plan
    plan.create_plan(lib, run, items, author, rationale)   # idempotent
    for item in plan.pending(lib, run):
        # (re-)boot at the top of each step so a dead kernel reconnects (see the
        # MASTER_PROMPT "Surviving the sandbox" rules), then:
        do_one(item)
        plan.mark(lib, run, item, "done", author, f"completed {item}")  # committed
    # killed mid-loop? just re-run: pending() skips the items already marked done.

A plan is ONE curated KU ``curated:plan/<run>`` whose body is JSON::

    {"run": <run>, "title": <str>, "items": [
        {"id": <str>, "status": "pending"|"done"|"skip", "note": <str>}, ...]}

It lives in the curated tier (durable, versioned, updatable via the Librarian),
and is also the human-readable "plan it keeps and modifies" — read it any time
to see where the work stands.
"""
import json

from .schema import KnowledgeUnit

_STATUSES = {"pending", "done", "skip"}


def plan_id(run) -> str:
    return f"curated:plan/{run}"


def load_plan(lib, run):
    """The plan dict for ``run``, or ``None`` if there is no active plan."""
    pid = plan_id(run)
    ku = lib.get(pid)
    if ku is None or getattr(ku, "status", "active") != "active":
        return None
    body = lib.read_body(pid)
    if not body:
        return None
    try:
        data = json.loads(body if isinstance(body, str) else body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _norm_items(items) -> list:
    out = []
    for it in items:
        if isinstance(it, dict):
            out.append({"id": str(it["id"]),
                        "status": it.get("status", "pending"),
                        "note": it.get("note", "")})
        else:
            out.append({"id": str(it), "status": "pending", "note": ""})
    return out


def _dump(plan) -> str:
    return json.dumps(plan, ensure_ascii=False, indent=2)


def create_plan(lib, run, items, author, rationale, title=""):
    """Create the plan for ``run`` if it does not already exist (IDEMPOTENT — an
    existing in-progress plan is never clobbered; returns it unchanged). ``items``
    is a list of item ids (str) or ``{"id","status","note"}`` dicts. Returns the
    plan dict."""
    existing = load_plan(lib, run)
    if existing is not None:
        return existing
    plan = {"run": run, "title": title, "items": _norm_items(items)}
    ku = KnowledgeUnit(
        id=plan_id(run), kind="curated-note", tier="curated", source="agent",
        path=f"kb/curated/plan/{run}.json", title=title or f"plan: {run}",
        confidence="VERIFIED")
    lib.begin(author, rationale).add_ku(ku, body=_dump(plan)).commit()
    return plan


def pending(lib, run) -> list:
    """Item ids still ``pending`` (the work left to do) — empty if no such plan."""
    p = load_plan(lib, run)
    return [it["id"] for it in (p["items"] if p else []) if it.get("status") == "pending"]


def mark(lib, run, item, status, author, rationale, note=""):
    """Set ``item``'s status (``pending``/``done``/``skip``) and commit it — so it
    is durable the instant this returns. An item not yet in the plan is appended.
    Returns the updated plan dict. Raises if the plan or status is invalid."""
    if status not in _STATUSES:
        raise ValueError(f"status {status!r} not in {sorted(_STATUSES)}")
    p = load_plan(lib, run)
    if p is None:
        raise LookupError(f"no active plan for run {run!r} — create_plan first")
    found = False
    for it in p["items"]:
        if it.get("id") == str(item):
            it["status"] = status
            if note:
                it["note"] = note
            found = True
            break
    if not found:
        p["items"].append({"id": str(item), "status": status, "note": note})
    lib.begin(author, rationale).update_ku(plan_id(run), body=_dump(p)).commit()
    return p


def progress(lib, run) -> dict:
    """Counts ``{"pending","done","skip","total"}`` for the run's plan."""
    p = load_plan(lib, run)
    items = p["items"] if p else []
    out = {"pending": 0, "done": 0, "skip": 0, "total": len(items)}
    for it in items:
        st = it.get("status", "pending")
        if st in out:
            out[st] += 1
    return out
