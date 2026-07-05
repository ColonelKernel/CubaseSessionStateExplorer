"""Readable, dialect-namespaced id generation.

Canonical ids are namespaced by dialect (``cubase:track-1``) so a Cubase
session can share a graph, diff, or dataset with REAPER/Ableton/Logic sessions
without id collisions. Native Cubase ids (from a DAWproject ``id`` attribute,
say) are preserved untouched inside ``raw_source`` / ``native``.
"""

from __future__ import annotations

import itertools

DIALECT = "cubase"

_counters: dict[str, itertools.count] = {}


def make_id(prefix: str) -> str:
    """Generate a readable sequential id like ``cubase:track-3``."""
    counter = _counters.setdefault(prefix, itertools.count(1))
    return f"{DIALECT}:{prefix}-{next(counter)}"


def reset_ids() -> None:
    """Reset counters (deterministic tests / demo builds)."""
    _counters.clear()


def stable_id(prefix: str, *parts: str) -> str:
    """Build a deterministic id from stable source parts (e.g. a DAWproject id).

    Preferred over :func:`make_id` when a source-stable key exists, because it
    survives a re-ingest and therefore keeps snapshot diffs meaningful across
    edits (a weakness we saw in the name-matching diffs of the prior prototypes).
    """
    from .utils import slugify

    key = "-".join(slugify(p) for p in parts if p)
    return f"{DIALECT}:{prefix}-{key}" if key else make_id(prefix)
