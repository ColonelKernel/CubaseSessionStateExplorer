"""Build an interpretable typed graph from a canonical Cubase SessionState.

Nodes and edges preserve Cubase-native distinctions the prior prototypes
flatten: a folder that only organizes (``CONTAINS``) is a different edge from a
group bus that sums signal (``SUMS`` / ``ROUTES_TO``); a send (``SENDS_TO``) is
not parent containment. networkx is optional — ``graph_to_dict`` produces a
JSON-serializable graph without it, so the core stays importable.
"""

from __future__ import annotations

from typing import Any, Optional

from .models import SessionState

# Observability colours reused from the Logic prototype's visual language.
OBSERVABILITY_COLORS = {
    "observed": "#2E86DE",
    "exported": "#1F9D6B",
    "parsed": "#27AE60",
    "inferred": "#F39C12",
    "reconstructed": "#E67E22",
    "user_supplied": "#9B59B6",
    "unavailable": "#C0392B",
    "conflicting": "#E74C3C",
}

NODE_TYPES = (
    "project", "track", "group", "fx_channel", "folder", "master",
    "device", "instrument", "parameter", "clip", "midi_part", "media",
    "send", "automation_lane", "marker", "chord", "unknown_state",
)


def _status(entity) -> str:
    prov = getattr(entity, "provenance", None)
    return getattr(prov, "status", "parsed") if prov else "parsed"


def build_graph_dict(session: SessionState) -> dict[str, Any]:
    """Return {"nodes":[...], "edges":[...], "metadata":{...}} — no deps."""
    nodes: list[dict] = []
    edges: list[dict] = []

    def node(nid: str, ntype: str, label: str, **attrs) -> None:
        nodes.append({"id": nid, "type": ntype, "label": label,
                      "color": OBSERVABILITY_COLORS.get(attrs.get("status", "parsed"), "#888"),
                      **attrs})

    def edge(src: str, dst: str, etype: str, **attrs) -> None:
        edges.append({"source": src, "target": dst, "type": etype, **attrs})

    pid = "project"
    node(pid, "project", session.project.project_name,
         tempo=session.tempo, time_signature=session.time_signature,
         cubase_version=session.project.cubase_version,
         coverage=session.capture.coverage_percent, status="parsed")

    # tracks / groups / fx / master
    def add_track_node(t, ntype: str) -> None:
        node(t.id, ntype, t.name, track_type=t.track_type, role=t.role,
             volume_db=t.volume_db, pan=t.pan, mute=t.mute, solo=t.solo,
             color=t.color, status=_status(t))
        # containment: project contains top-level; folder contains children
        if t.parent_id:
            edge(t.parent_id, t.id, "CONTAINS")
        else:
            edge(pid, t.id, "CONTAINS")
        for d in t.devices:
            dtype = "instrument" if d.device_type == "instrument" else "device"
            node(d.id, dtype, d.name, index=d.index, family=d.device_family,
                 enabled=d.enabled, plugin_format=d.plugin_format, status=_status(d))
            edge(d.id, t.id, "INSERTED_ON", slot=d.index)
            for p in d.parameters:
                node(p.id, "parameter", p.name, value=p.value, unit=p.unit,
                     status=_status(p))
                edge(p.id, d.id, "CONTROLLED_BY")
        for c in t.clips:
            ctype = "midi_part" if c.clip_type == "midi" else "clip"
            node(c.id, ctype, c.name, start=c.start_time_beats,
                 length=c.length_beats, notes=c.midi_note_count, status=_status(c))
            edge(t.id, c.id, "CONTAINS")
            if c.audio_file:
                mid = f"media:{c.audio_file}"
                if not any(n["id"] == mid for n in nodes):
                    node(mid, "media", c.audio_file.rsplit("/", 1)[-1], path=c.audio_file,
                         status="parsed")
                edge(c.id, mid, "USES_MEDIA")
        for s in t.sends:
            sid = s.id
            node(sid, "send", s.send_name or "send", level_db=s.level_db,
                 enabled=s.enabled, pre_fader=s.pre_fader, status=_status(s))
            edge(t.id, sid, "SENDS_TO")
            if s.target_return_id:
                edge(sid, s.target_return_id, "SENDS_TO", level_db=s.level_db)

    for t in session.tracks:
        add_track_node(t, "track")
    for t in session.groups:
        add_track_node(t, "group")
    for t in session.return_tracks:
        add_track_node(t, "fx_channel")
    if session.master_track:
        add_track_node(session.master_track, "master")

    # explicit output routing edges (bus/group topology)
    for r in session.routes:
        etype = "SUMS" if _is_group(session, r.target_id) else "ROUTES_TO"
        edge(r.source_track_id, r.target_id, etype, route_type=r.route_type,
             status=_status(r))

    # folders (organizational containers, distinct from group buses)
    for f in session.folders:
        node(f.id, "folder", f.name,
             group_channel_enabled=f.group_channel_enabled,
             organizational_only=f.organizational_only, status=_status(f))
        edge(pid, f.id, "CONTAINS")
        for cid in f.child_track_ids:
            edge(f.id, cid, "SUMS" if f.group_channel_enabled else "CONTAINS")

    # automation lanes: PARAMETER -> LANE -> (device/track)
    for a in session.automation:
        node(a.id, "automation_lane", a.parameter_name, points=a.point_count,
             device_id=a.device_id, status=_status(a))
        target = a.device_id or a.track_id
        edge(a.id, target, "AUTOMATES")

    # musical structure
    for m in session.musical_structure.markers:
        node(m.id, "marker", m.name or f"@{m.time_beats}", time=m.time_beats,
             cycle=m.cycle, status="parsed")
        edge(pid, m.id, "CONTAINS")

    # unknown state markers (the observability boundary, made visible)
    for u in session.unknown_state:
        uid = u.id
        node(uid, "unknown_state", u.state_gap, reason=u.reason,
             severity=u.severity, status="unavailable")
        edge(u.entity_id or pid, uid, "HAS_GAP")

    metadata = {
        "n_nodes": len(nodes),
        "n_edges": len(edges),
        "n_tracks": len(session.tracks),
        "n_groups": len(session.groups),
        "n_fx": len(session.return_tracks),
        "n_devices": len(session.all_devices()),
        "n_sends": len(session.all_sends()),
        "n_automation": len(session.automation),
        "n_unknown": len(session.unknown_state),
        "coverage_percent": session.capture.coverage_percent,
        "status_counts": _status_counts(nodes),
    }
    return {"nodes": nodes, "edges": edges, "metadata": metadata}


def _is_group(session: SessionState, tid: str) -> bool:
    t = session.track_by_id(tid)
    return bool(t and t.track_type in ("group", "fx", "master"))


def _status_counts(nodes: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for n in nodes:
        st = n.get("status", "parsed")
        out[st] = out.get(st, 0) + 1
    return out


def build_networkx(session: SessionState):
    """Optional: return a networkx.DiGraph. Raises ImportError if unavailable."""
    import networkx as nx

    data = build_graph_dict(session)
    g = nx.DiGraph()
    for n in data["nodes"]:
        g.add_node(n["id"], **{k: v for k, v in n.items() if k != "id"})
    for e in data["edges"]:
        g.add_edge(e["source"], e["target"], **{k: v for k, v in e.items()
                                                 if k not in ("source", "target")})
    g.graph.update(data["metadata"])
    return g
