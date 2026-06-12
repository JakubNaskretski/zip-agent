"""Progress narration for long digests (the sandbox survival rules —
MASTER_PROMPT §4 "Long operations").

Slow code-interpreter sandboxes hard-kill long executions, and a big digest is
silent for its whole runtime — the host model (and the user) learn nothing
until the call returns or dies. Every ingest entry point therefore takes a
``progress`` callable (default ``None`` = strict no-op, zero overhead): pass
``progress=print`` and the dominant loops emit a one-line count every
``EVERY`` files/KUs, so a killed call shows how far it got.
"""
from __future__ import annotations

EVERY = 1000   # files/KUs between progress lines


def extract_in_chunks(builder, paths, root, progress, label):
    """``builder.extract_files`` in ``EVERY``-sized slices, narrating between
    slices. Behavior-identical to a single call: extraction is per-file, so
    ``extracted``/``errors`` concatenate in input order.

    The final line always ends with "done — N/N files scanned, M handled" so
    callers can detect completion even if intermediate ticks are sparse."""
    paths = list(paths)
    if progress is None:
        return builder.extract_files(paths, root=root)
    extracted: list = []
    errors: list = []
    total = len(paths)
    for i in range(0, total, EVERY):
        ex, er = builder.extract_files(paths[i:i + EVERY], root=root)
        extracted.extend(ex)
        errors.extend(er)
        done = min(i + EVERY, total)
        if done == total:
            progress(f"{label}: done — {done}/{total} files scanned, "
                     f"{len(extracted)} handled")
        else:
            progress(f"{label}: {done}/{total} files scanned, "
                     f"{len(extracted)} handled")
    return extracted, errors


def tick(progress, label, count):
    """Fire a one-line count every ``EVERY`` items in a staging loop."""
    if progress is not None and count % EVERY == 0:
        progress(f"{label}: {count} KUs staged")


def done(progress, label, count):
    """Emit one compact final line after a staging loop completes.

    Skips the line when ``count`` is an exact multiple of ``EVERY`` (the last
    ``tick`` call already fired at that boundary — avoids a duplicate). When
    ``count`` is 0 (empty loop) the line is also skipped.
    If ``progress`` is ``None`` this is a no-op."""
    if progress is None or count == 0:
        return
    if count % EVERY == 0:
        return   # last tick already fired at this boundary
    progress(f"{label}: done — {count} KUs staged")
