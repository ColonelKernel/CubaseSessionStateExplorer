"""Snapshot persistence: canonical SessionState <-> JSON on disk.

A snapshot is the full validated SessionState plus its graph, saved so two
snapshots (before/after a Cubase edit) can be diffed later. Stable, source-
derived ids (see :mod:`ids`) keep entities matchable across a re-ingest.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .graph_builder import build_graph_dict
from .models import SessionState, validate_session_dict


def save_snapshot(session: SessionState, path: str, *, include_graph: bool = True) -> str:
    payload: dict[str, Any] = {
        "schema_version": session.schema_version,
        "session": session.model_dump(mode="json"),
    }
    if include_graph:
        payload["graph"] = build_graph_dict(session)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return path


def load_snapshot(path: str) -> SessionState:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    data = payload.get("session", payload)
    return validate_session_dict(data)
