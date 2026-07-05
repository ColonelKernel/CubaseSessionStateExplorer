"""Streamlit explorer for Cubase session state.

A technical instrument, not a consumer dashboard: it foregrounds provenance and
the observability boundary. Run:

    pip install -e ".[ui,audio]"
    streamlit run src/cubase_session_explorer/app.py

Everything degrades gracefully: without pyvis/plotly the graph renders as a
node/edge table; without pandas, as plain lists.
"""

from __future__ import annotations

import glob
import json
import os

import streamlit as st
import streamlit.components.v1 as components

from cubase_session_explorer import __version__
from cubase_session_explorer.audio_descriptors import descriptor_delta, extract as extract_audio
from cubase_session_explorer.diff import diff_sessions
from cubase_session_explorer.fusion import ingest
from cubase_session_explorer.graph_builder import OBSERVABILITY_COLORS, build_graph_dict
from cubase_session_explorer.report import extraction_report

st.set_page_config(page_title="Cubase Session State Explorer", layout="wide")

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "fixtures", "cubase")


# --------------------------------------------------------------------------

def _table(rows, caption=None):
    if caption:
        st.markdown(f"**{caption}**")
    if not rows:
        st.caption("— none —")
        return
    try:
        import pandas as pd
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    except Exception:
        st.table(rows)


def _name(session, tid):
    t = session.track_by_id(tid) if tid else None
    return t.name if t else (tid or "")


def _fixture_options():
    return sorted(glob.glob(os.path.join(FIX, "*.dawproject")))


@st.cache_data(show_spinner=False)
def _ingest(path: str):
    res = ingest(path)
    return res.session.model_dump(mode="json")


def _load(path: str):
    from cubase_session_explorer.models import SessionState
    return SessionState.model_validate(_ingest(path))


# --------------------------------------------------------------------------

st.title("Cubase Session State Explorer")
st.caption(f"v{__version__} · the Cubase adapter in the cross-DAW Session State "
           "Explorer family (REAPER · Ableton · Logic · **Cubase**)")

with st.sidebar:
    st.header("Ingest")
    opts = _fixture_options()
    labels = [os.path.basename(o) for o in opts]
    pick = st.selectbox("Bundle / .dawproject", labels) if labels else None
    custom = st.text_input("…or a path", "")
    path = custom.strip() or (opts[labels.index(pick)] if pick else "")

    st.divider()
    st.header("Filters")
    show_types = st.multiselect(
        "Node types",
        ["project", "track", "group", "fx_channel", "master", "device",
         "instrument", "parameter", "clip", "midi_part", "media", "send",
         "automation_lane", "marker", "unknown_state"],
        default=["project", "track", "group", "fx_channel", "master", "device",
                 "send", "automation_lane", "unknown_state"],
    )
    status_filter = st.multiselect(
        "Provenance status", list(OBSERVABILITY_COLORS.keys()),
        default=list(OBSERVABILITY_COLORS.keys()),
    )

if not path or not os.path.exists(path):
    st.info("Pick a fixture in the sidebar, or run `python tools/make_fixtures.py` "
            "to generate them. You can also point at any `.dawproject`, `.cpr`, "
            "Track Archive `.xml`, or a bundle directory.")
    st.stop()

session = _load(path)
graph = build_graph_dict(session)

tab_overview, tab_graph, tab_tables, tab_provenance, tab_unknown, tab_compare = st.tabs(
    ["Overview", "Graph", "Tables", "Provenance", "Unknown state", "Compare / Experiment"]
)

# ---- Overview ------------------------------------------------------------
with tab_overview:
    c = st.columns(5)
    c[0].metric("Tracks", len(session.tracks))
    c[1].metric("Groups / FX", len(session.groups) + len(session.return_tracks))
    c[2].metric("Inserts", len(session.all_devices()))
    c[3].metric("Automation lanes", len(session.automation))
    c[4].metric("Coverage", f"{session.capture.coverage_percent}%")
    st.progress(min(1.0, (session.capture.coverage_percent or 0) / 100))
    st.code(extraction_report(session), language="text")

# ---- Graph ---------------------------------------------------------------
with tab_graph:
    nodes = [n for n in graph["nodes"]
             if n["type"] in show_types and n.get("status", "parsed") in status_filter]
    keep = {n["id"] for n in nodes}
    edges = [e for e in graph["edges"] if e["source"] in keep and e["target"] in keep]
    st.caption(f"{len(nodes)} nodes · {len(edges)} edges · colour = provenance status")

    rendered = False
    try:
        from pyvis.network import Network
        import tempfile
        net = Network(height="640px", width="100%", directed=True, bgcolor="#111", font_color="#eee")
        for n in nodes:
            net.add_node(n["id"], label=n["label"],
                         color=n.get("color", "#888"),
                         title=f"{n['type']} · {n.get('status','')}")
        for e in edges:
            net.add_edge(e["source"], e["target"], label=e["type"], arrows="to")
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as fh:
            net.save_graph(fh.name)
            html = open(fh.name).read()
        components.html(html, height=660)
        rendered = True
    except Exception:
        pass

    if not rendered:
        st.info("pyvis not installed — showing node/edge tables (install `.[ui]` for the interactive graph).")
        _table([{"id": n["id"], "type": n["type"], "label": n["label"],
                 "status": n.get("status")} for n in nodes], "Nodes")
        _table([{"from": e["source"], "edge": e["type"], "to": e["target"]}
                for e in edges], "Edges")

    st.markdown("**Legend** — " + " · ".join(
        f":{'red' if k in ('unavailable','conflicting') else 'blue'}[{k}]"
        for k in OBSERVABILITY_COLORS))

# ---- Tables --------------------------------------------------------------
with tab_tables:
    _table([{"name": t.name, "type": t.track_type, "role": t.role,
             "vol_dB": t.volume_db, "pan": t.pan, "mute": t.mute,
             "output": t.output_target_id} for t in session.all_tracks()], "Tracks")
    _table([{"track": session.track_by_id(d.track_id).name if session.track_by_id(d.track_id) else d.track_id,
             "slot": d.index, "plug-in": d.name, "family": d.device_family,
             "format": d.plugin_format, "enabled": d.enabled} for d in session.all_devices()], "Inserts / Instruments")
    _table([{"source": _name(session, s.source_track_id), "→ target": _name(session, s.target_return_id),
             "level_dB": s.level_db, "pre_fader": s.pre_fader} for s in session.all_sends()], "Sends")
    _table([{"source": _name(session, r.source_track_id), "→ target": _name(session, r.target_id),
             "type": r.route_type} for r in session.routes], "Output routing")
    _table([{"parameter": a.parameter_name, "track": _name(session, a.track_id),
             "points": a.point_count} for a in session.automation], "Automation")

# ---- Provenance ----------------------------------------------------------
with tab_provenance:
    st.caption("Select any entity to answer: **where did this value come from?**")
    entity_names = [t.name for t in session.all_tracks()]
    sel = st.selectbox("Track", entity_names) if entity_names else None
    if sel:
        t = next(x for x in session.all_tracks() if x.name == sel)
        st.write(f"**{t.name}** — canonical `{t.track_type}`, "
                 f"native `{t.native.get('cubase', {})}`")
        rows = [{"field": "entity", "status": t.provenance.status,
                 "confidence": t.provenance.confidence,
                 "source": t.provenance.source.short() if t.provenance.source else "",
                 "why": t.provenance.explanation or ""}]
        for f, p in t.field_provenance.items():
            rows.append({"field": f, "status": p.status, "confidence": p.confidence,
                         "source": p.source.short() if p.source else "",
                         "why": p.explanation or ""})
        _table(rows, "Field provenance")
        for d in t.devices:
            with st.expander(f"insert {d.index}: {d.name}"):
                st.write(f"status `{d.provenance.status}` · source "
                         f"`{d.provenance.source.short() if d.provenance.source else '?'}`")
                pgap = d.field_provenance.get("parameters")
                if pgap:
                    st.warning(f"parameters: {pgap.status} — {pgap.explanation}")

# ---- Unknown state -------------------------------------------------------
with tab_unknown:
    st.caption("The observability boundary is a first-class research object.")
    _table([{"gap": u.state_gap, "entity": u.entity_id or "session",
             "severity": u.severity, "reason": u.reason,
             "potential sources": ", ".join(u.potential_sources)}
            for u in session.unknown_state], "Unknown / unavailable state")

# ---- Compare / Experiment ------------------------------------------------
with tab_compare:
    st.caption("Diff two snapshots and (optionally) link the state delta to an "
               "audio delta — the seed of the state→acoustic research loop.")
    other = st.selectbox("Compare against", [os.path.basename(o) for o in opts],
                         index=min(1, len(opts) - 1) if len(opts) > 1 else 0)
    other_path = opts[[os.path.basename(o) for o in opts].index(other)]
    b = _load(other_path)
    result = diff_sessions(session, b)
    st.write(f"**{session.project.project_name} → {b.project.project_name}**")
    st.write("Change categories:", result.summary() or "no changes")
    for line in result.narrative():
        st.text(line)

    ra = path.replace(".dawproject", ".wav")
    rb = other_path.replace(".dawproject", ".wav")
    if os.path.exists(ra) and os.path.exists(rb):
        st.divider()
        st.write("**Audio delta** (matching renders found):")
        da, db = extract_audio(ra, "a"), extract_audio(rb, "b")
        _table([{"descriptor": k, "a": v["a"], "b": v["b"], "Δ": v["delta"]}
                for k, v in descriptor_delta(da, db).items()], "Acoustic descriptor delta")
