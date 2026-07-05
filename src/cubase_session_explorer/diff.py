"""Snapshot diff engine with typed change classification.

Compares two canonical SessionStates by **stable id** (falling back to name),
and classifies every change into a research-meaningful category:

  STRUCTURAL  tracks/devices/fx added or removed, insert reordering
  PARAMETER   a plug-in / automation parameter value changed
  TEMPORAL    a clip/event moved or resized
  ROUTING     output routing or a send changed
  MUSICAL     tempo, notes, chords, markers changed
  MIXER       volume / pan / mute / solo changed
  UNKNOWN     a state gap changed (observability shifted)

This taxonomy is the bridge to the intervention model: a known StateIntervention
should produce a predictable diff category.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .models import SessionState, TrackState

ChangeCategory = str  # one of the categories above


@dataclass
class Change:
    category: ChangeCategory
    op: str                       # "added" | "removed" | "changed"
    target: str                   # human path e.g. "track:Lead Vox/insert:1"
    before: Any = None
    after: Any = None
    detail: str = ""

    def as_line(self) -> str:
        sign = {"added": "+", "removed": "-", "changed": "~"}.get(self.op, "~")
        body = self.target
        if self.op == "changed":
            body += f": {self.before!r} -> {self.after!r}"
        elif self.detail:
            body += f": {self.detail}"
        return f"{sign} [{self.category}] {body}"


@dataclass
class DiffResult:
    changes: list[Change] = field(default_factory=list)

    def by_category(self) -> dict[str, list[Change]]:
        out: dict[str, list[Change]] = {}
        for c in self.changes:
            out.setdefault(c.category, []).append(c)
        return out

    def summary(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.by_category().items()}

    def narrative(self) -> list[str]:
        return [c.as_line() for c in self.changes]


def _track_key(t: TrackState) -> str:
    return t.id or f"name:{t.name.lower()}"


def _index_tracks(s: SessionState) -> dict[str, TrackState]:
    idx: dict[str, TrackState] = {}
    for t in s.all_tracks():
        idx[_track_key(t)] = idx.setdefault(_track_key(t), t)
        idx[f"name:{t.name.lower()}"] = t
    return idx


def diff_sessions(a: SessionState, b: SessionState) -> DiffResult:
    result = DiffResult()
    _diff_project(a, b, result)
    _diff_tracks(a, b, result)
    _diff_routing(a, b, result)
    _diff_automation(a, b, result)
    _diff_musical(a, b, result)
    _diff_unknown(a, b, result)
    return result


def _diff_project(a: SessionState, b: SessionState, r: DiffResult) -> None:
    if a.tempo != b.tempo:
        r.changes.append(Change("MUSICAL", "changed", "project/tempo", a.tempo, b.tempo))
    if a.time_signature != b.time_signature:
        r.changes.append(Change("MUSICAL", "changed", "project/time_signature",
                                a.time_signature, b.time_signature))


def _match_tracks(a: SessionState, b: SessionState):
    a_idx = {_track_key(t): t for t in a.all_tracks()}
    b_idx = {_track_key(t): t for t in b.all_tracks()}
    a_names = {t.name.lower(): t for t in a.all_tracks()}
    b_names = {t.name.lower(): t for t in b.all_tracks()}
    matched: list[tuple[TrackState, TrackState]] = []
    a_used, b_used = set(), set()
    for key, ta in a_idx.items():
        tb = b_idx.get(key)
        if tb is None:
            tb = b_names.get(ta.name.lower())
        if tb is not None and id(tb) not in b_used:
            matched.append((ta, tb))
            a_used.add(id(ta)); b_used.add(id(tb))
    added = [t for t in b.all_tracks() if id(t) not in b_used]
    removed = [t for t in a.all_tracks() if id(t) not in a_used]
    return matched, added, removed


def _diff_tracks(a: SessionState, b: SessionState, r: DiffResult) -> None:
    matched, added, removed = _match_tracks(a, b)
    for t in added:
        r.changes.append(Change("STRUCTURAL", "added", f"track:{t.name}",
                                detail=f"{t.track_type} track"))
    for t in removed:
        r.changes.append(Change("STRUCTURAL", "removed", f"track:{t.name}",
                                detail=f"{t.track_type} track"))
    for ta, tb in matched:
        _diff_one_track(ta, tb, r)


def _diff_one_track(ta: TrackState, tb: TrackState, r: DiffResult) -> None:
    name = tb.name
    for f, cat in (("volume_db", "MIXER"), ("pan", "MIXER"),
                   ("mute", "MIXER"), ("solo", "MIXER")):
        va, vb = getattr(ta, f), getattr(tb, f)
        if va != vb and not (va is None and vb is None):
            r.changes.append(Change(cat, "changed", f"track:{name}/{f}", va, vb))

    # devices (by name+slot for stability)
    a_dev = {(d.name, d.index): d for d in ta.devices}
    b_dev = {(d.name, d.index): d for d in tb.devices}
    a_names = [d.name for d in ta.devices]
    b_names = [d.name for d in tb.devices]
    for key, d in b_dev.items():
        if key not in a_dev and d.name not in a_names:
            r.changes.append(Change("STRUCTURAL", "added",
                                    f"track:{name}/insert:{d.index}", detail=d.name))
    for key, d in a_dev.items():
        if key not in b_dev and d.name not in b_names:
            r.changes.append(Change("STRUCTURAL", "removed",
                                    f"track:{name}/insert:{d.index}", detail=d.name))
    if a_names != b_names and set(a_names) == set(b_names):
        r.changes.append(Change("STRUCTURAL", "changed", f"track:{name}/insert_order",
                                a_names, b_names, detail="inserts reordered"))

    # device parameter VALUE changes (readable/enumerable params only)
    for key, db in b_dev.items():
        da = a_dev.get(key)
        if da is None:
            continue
        pa = {p.name: p.value for p in da.parameters}
        pb = {p.name: p.value for p in db.parameters}
        for pname, vb in pb.items():
            va = pa.get(pname)
            if pname in pa and va != vb:
                r.changes.append(Change(
                    "PARAMETER", "changed",
                    f"track:{name}/insert:{db.index}/{db.name}/{pname}", va, vb))

    # clip timing (temporal)
    a_clip = {c.name: c for c in ta.clips}
    for c in tb.clips:
        ca = a_clip.get(c.name)
        if ca is None:
            continue
        if ca.start_time_beats != c.start_time_beats:
            r.changes.append(Change("TEMPORAL", "changed", f"track:{name}/clip:{c.name}/start",
                                    ca.start_time_beats, c.start_time_beats))
        if ca.length_beats != c.length_beats:
            r.changes.append(Change("TEMPORAL", "changed", f"track:{name}/clip:{c.name}/length",
                                    ca.length_beats, c.length_beats))
        if (ca.midi_note_count or 0) != (c.midi_note_count or 0):
            r.changes.append(Change("MUSICAL", "changed", f"track:{name}/clip:{c.name}/notes",
                                    ca.midi_note_count, c.midi_note_count))

    # sends (routing)
    a_send = {s.target_return_id: s for s in ta.sends}
    b_send = {s.target_return_id: s for s in tb.sends}
    for tgt, s in b_send.items():
        if tgt not in a_send:
            r.changes.append(Change("ROUTING", "added", f"track:{name}/send",
                                    detail=f"-> {s.send_name or tgt} @ {s.level_db} dB"))
        elif a_send[tgt].level_db != s.level_db:
            r.changes.append(Change("ROUTING", "changed", f"track:{name}/send:{tgt}/level_db",
                                    a_send[tgt].level_db, s.level_db))
    for tgt, s in a_send.items():
        if tgt not in b_send:
            r.changes.append(Change("ROUTING", "removed", f"track:{name}/send",
                                    detail=f"-> {s.send_name or tgt}"))

    if ta.output_target_id != tb.output_target_id:
        r.changes.append(Change("ROUTING", "changed", f"track:{name}/output",
                                ta.output_target_id, tb.output_target_id))


def _diff_routing(a: SessionState, b: SessionState, r: DiffResult) -> None:
    a_routes = {(x.source_track_id, x.target_id) for x in a.routes}
    b_routes = {(x.source_track_id, x.target_id) for x in b.routes}
    for edge in b_routes - a_routes:
        r.changes.append(Change("ROUTING", "added", f"route:{edge[0]}->{edge[1]}"))
    for edge in a_routes - b_routes:
        r.changes.append(Change("ROUTING", "removed", f"route:{edge[0]}->{edge[1]}"))


def _diff_automation(a: SessionState, b: SessionState, r: DiffResult) -> None:
    a_lanes = {(x.track_id, x.parameter_name): x for x in a.automation}
    b_lanes = {(x.track_id, x.parameter_name): x for x in b.automation}
    for key, lane in b_lanes.items():
        la = a_lanes.get(key)
        label = f"automation:{lane.parameter_name}"
        if la is None:
            r.changes.append(Change("PARAMETER", "added", label,
                                    detail=f"{lane.point_count} points"))
            continue
        if la.point_count != lane.point_count:
            r.changes.append(Change("PARAMETER", "changed", f"{label}/point_count",
                                    la.point_count, lane.point_count))
        # single-point parameter value change (our DualFilter Position case)
        av = [round(p.value, 4) for p in la.points]
        bv = [round(p.value, 4) for p in lane.points]
        if av != bv:
            r.changes.append(Change("PARAMETER", "changed", f"{label}/values", av, bv))
    for key, lane in a_lanes.items():
        if key not in b_lanes:
            r.changes.append(Change("PARAMETER", "removed", f"automation:{lane.parameter_name}"))


def _diff_musical(a: SessionState, b: SessionState, r: DiffResult) -> None:
    am = {m.name: m for m in a.musical_structure.markers}
    bm = {m.name: m for m in b.musical_structure.markers}
    for name in bm.keys() - am.keys():
        r.changes.append(Change("MUSICAL", "added", f"marker:{name}"))
    for name in am.keys() - bm.keys():
        r.changes.append(Change("MUSICAL", "removed", f"marker:{name}"))


def _diff_unknown(a: SessionState, b: SessionState, r: DiffResult) -> None:
    a_gaps = {u.state_gap for u in a.unknown_state}
    b_gaps = {u.state_gap for u in b.unknown_state}
    for g in b_gaps - a_gaps:
        r.changes.append(Change("UNKNOWN", "added", f"state_gap:{g}",
                                detail="became unobservable"))
    for g in a_gaps - b_gaps:
        r.changes.append(Change("UNKNOWN", "removed", f"state_gap:{g}",
                                detail="became observable"))
