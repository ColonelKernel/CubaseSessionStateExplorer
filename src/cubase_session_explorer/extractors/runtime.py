"""MIDI Remote runtime snapshot loader.

The Cubase MIDI Remote API (ES5 JavaScript, ``mHostAccess``) can observe the
*selected* mixer channel (name, volume, pan, mute/solo) and transport, and emit
that as structured data over a MIDI/virtual bridge. Our companion probe script
(``runtime/cubase-state-probe.js`` + ``runtime/bridge_listener.py``) writes those
observations to a JSON snapshot. This loader ingests that snapshot.

Crucially, this is a *control-surface* API, not a project-model API: it sees one
channel at a time and cannot enumerate routing, insert slots, or arbitrary VST
parameters. We reflect that boundary honestly in provenance + unknown_state.

Snapshot schema (produced by the bridge)::

    {
      "cubase_version": "15.0.10",
      "captured_at": "2026-07-05T12:00:00Z",
      "transport": {"tempo": 120.0, "playing": false},
      "channels": [
        {"name": "Lead Vox", "volume_db": -2.5, "pan": 0.0,
         "mute": false, "solo": false, "selected": true}
      ],
      "capability": { ... echoed capability-probe findings ... }
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from ..ids import stable_id
from ..models import ProjectMeta, SessionState, TrackState
from ..provenance import observed

RUNTIME_SOURCE = "midi_remote"


@dataclass
class RuntimeResult:
    ok: bool = False
    session: Optional[SessionState] = None
    capability: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def extract(path: str) -> RuntimeResult:
    result = RuntimeResult()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        result.warnings.append(f"Cannot read runtime snapshot: {exc}")
        return result

    session = SessionState(
        project=ProjectMeta(
            project_name=payload.get("project_name", "Runtime Capture"),
            cubase_version=payload.get("cubase_version"),
        )
    )
    session.provenance = observed(RUNTIME_SOURCE, artifact=path.rsplit("/", 1)[-1])
    session.capture.artifacts.append(RUNTIME_SOURCE)
    session.capture.extractors_run.append("runtime")
    session.capture.captured_at = payload.get("captured_at")

    transport = payload.get("transport") or {}
    if "tempo" in transport:
        session.tempo = transport["tempo"]

    for i, ch in enumerate(payload.get("channels", [])):
        track = TrackState(
            id=stable_id("track", f"rt-{ch.get('name', i)}"),
            index=i,
            name=ch.get("name") or f"Channel {i + 1}",
            track_type="audio",
            volume_db=ch.get("volume_db"),
            pan=ch.get("pan"),
            mute=ch.get("mute"),
            solo=ch.get("solo"),
        )
        # These fields are genuinely observed (ground truth at capture time).
        for f in ("volume_db", "pan", "mute", "solo"):
            track.field_provenance[f] = observed(RUNTIME_SOURCE)
        track.provenance = observed(RUNTIME_SOURCE)
        track.native.setdefault("cubase", {})["selected_at_capture"] = ch.get("selected", False)
        session.tracks.append(track)

    result.capability = payload.get("capability", {})
    result.session = session
    result.ok = True
    return result
