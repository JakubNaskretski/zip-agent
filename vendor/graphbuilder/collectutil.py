"""Shared plumbing for the source collectors (Confluence, Jira).

Everything here is transport/dump mechanics with identical semantics across
sources: a urllib opener (with the opt-in insecure mode for self-signed on-prem
certs), JSON GET with throttle/transient retries, filesystem-safe segments, and
the incremental-dump helpers (prune + the ``.incomplete`` sentinel). Collectors
own everything source-specific: endpoints, auth env var, pagination shape, and
the per-unit unchanged check.
"""
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path

RETRY_CODES = {429, 502, 503, 504}     # throttling + transient gateway errors
# Sentinel marking a dump dir whose last collection aborted mid-listing, so a
# later build/prune knows the dump may be missing units.
INCOMPLETE_MARK = ".incomplete"


def build_opener(insecure: bool = False, ca_bundle=None):
    """Build a urllib opener.

    ``ca_bundle`` (a PEM file path) trusts a private/self-signed CA while keeping
    full verification — the RIGHT fix for on-prem certs. ``insecure=True``
    disables TLS verification entirely; last-resort only (MITM-exposed), kept as
    an explicit, documented opt-in. ``ca_bundle`` wins when both are given."""
    if ca_bundle:
        ctx = ssl.create_default_context(cafile=str(ca_bundle))
        return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    if insecure:
        ctx = ssl._create_unverified_context()
        return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener()


def safe_segment(name: str) -> str:
    """Filesystem-safe dir/file segment (no traversal / odd chars)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "_"


def _retry_after(exc) -> float:
    try:
        val = exc.headers.get("Retry-After")
        return float(val) if val else 0.0
    except Exception:
        return 0.0


def get_json(opener, url, headers, timeout, sleep, retries=3):
    """GET ``url`` and parse JSON, retrying on 429/502/503/504 (honouring
    ``Retry-After``) and transient network errors with exponential backoff.
    Raises after ``retries``."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    last = None
    for attempt in range(retries + 1):
        try:
            resp = opener.open(req, timeout=timeout)
            raw = resp.read()
            return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in RETRY_CODES and attempt < retries:
                sleep(_retry_after(exc) or (2 ** attempt))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if attempt < retries:
                sleep(2 ** attempt)
                continue
            raise
    raise last  # pragma: no cover - loop always returns or raises above


def prune_dir(dump_dir: Path, seen: set, suffix: str) -> list:
    """Delete ``*<suffix>`` dump files whose id/key a COMPLETE listing no longer
    returned — deleted/moved units would otherwise stay in the graph forever.
    Returns the pruned ids. Only ever touches files directly inside ``dump_dir``."""
    pruned = []
    if not dump_dir.is_dir():
        return pruned
    for f in sorted(dump_dir.glob(f"*{suffix}")):
        uid = f.name[: -len(suffix)]
        if uid not in seen:
            try:
                f.unlink()
                pruned.append(uid)
            except OSError:
                pass
    return pruned


def mark_incomplete(dump_dir: Path, complete: bool):
    """Maintain the ``.incomplete`` sentinel: written when a listing aborted
    (dump may be partial), removed by the next complete run."""
    mark = dump_dir / INCOMPLETE_MARK
    if complete:
        mark.unlink(missing_ok=True)
    elif dump_dir.is_dir():
        try:
            mark.write_text("last collection aborted mid-listing; dump may be missing pages\n",
                            encoding="utf-8")
        except OSError:
            pass
