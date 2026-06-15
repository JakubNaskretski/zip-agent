"""Durable worklist — drive multi-step work off a file that survives a reset.

The sandbox can kill a long call and take the in-memory state with it. For
anything more than a couple of steps (a big ingest, an RFP pass), keep the
worklist ON DISK and loop off it, committing each item, so a kernel death loses
at most the one in-flight item:

    from runtime import plan
    plan.create_plan(ws, "acme-rfp", ["req-001", "req-002", ...])   # idempotent
    for item in plan.pending(ws, "acme-rfp"):
        ... do one item ...
        plan.mark(ws, "acme-rfp", item)        # status→done, single-file write
    # killed mid-loop? re-run the loop — pending() skips items already done.

Each ``mark`` is one small file write (no repack), which is exactly what makes
the progress durable. ``dev/plan/<run>.json`` is also the human-readable record
of where the work stands."""
from __future__ import annotations

import json

PLAN_DIR = "dev/plan"


def _path(run: str) -> str:
    return f"{PLAN_DIR}/{run}.json"


def create_plan(ws, run: str, items, *, title=None) -> dict:
    """Create (or extend) the worklist for ``run``. Idempotent: an existing run's
    item statuses are preserved and only genuinely new items are appended — so
    re-running after a reset never resets progress."""
    path = _path(run)
    if ws.exists(path):
        cur = json.loads(ws.read_text(path))
        known = {i["item"] for i in cur.get("items", [])}
        for it in items:
            if it not in known:
                cur["items"].append({"item": it, "status": "pending"})
        ws.write_text(path, json.dumps(cur, indent=2))
        return cur
    plan = {"run": run, "title": title or run,
            "items": [{"item": it, "status": "pending"} for it in items]}
    ws.write_text(path, json.dumps(plan, indent=2))
    return plan


def load_plan(ws, run: str) -> dict:
    return json.loads(ws.read_text(_path(run)))


def pending(ws, run: str) -> list:
    """Items not yet marked done — the work still left to do."""
    return [i["item"] for i in load_plan(ws, run).get("items", [])
            if i.get("status") != "done"]


def mark(ws, run: str, item: str, status: str = "done") -> dict:
    """Set ``item``'s status (single-file write) and return the updated plan."""
    plan = load_plan(ws, run)
    for i in plan.get("items", []):
        if i["item"] == item:
            i["status"] = status
            break
    else:
        plan.setdefault("items", []).append({"item": item, "status": status})
    ws.write_text(_path(run), json.dumps(plan, indent=2))
    return plan


def progress(ws, run: str) -> dict:
    """``{"done": n, "total": m, "pending": [...]}`` for ``run``."""
    items = load_plan(ws, run).get("items", [])
    done = [i["item"] for i in items if i.get("status") == "done"]
    pend = [i["item"] for i in items if i.get("status") != "done"]
    return {"done": len(done), "total": len(items), "pending": pend}
