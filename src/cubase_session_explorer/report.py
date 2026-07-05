"""Human-readable extraction report + explainable coverage.

Generated on every ingest so a user can see, at a glance, what was recovered,
what was not, and *why* — with an honest, explainable coverage number (never a
fabricated "AI confidence score").
"""

from __future__ import annotations

from . import observation_model as om
from .models import SessionState


def extraction_report(session: SessionState) -> str:
    p = session.project
    lines: list[str] = []
    lines.append("CUBASE EXTRACTION REPORT")
    lines.append("=" * 60)
    lines.append(f"Project:            {p.project_name}")
    lines.append(f"Detected Cubase:    {p.cubase_version or 'unknown'}")
    lines.append(f"Tempo / TimeSig:    {session.tempo or '?'} bpm  {session.time_signature or '?'}")
    lines.append("")

    all_arts = set(session.capture.artifacts)
    lines.append("Artifacts:")
    for art in ("dawproject", "track_archive", "cpr", "midi_export",
                "musicxml_export", "midi_remote", "preset", "rendered_audio"):
        mark = "OK " if art in all_arts else "-- "
        lines.append(f"  [{mark}] {art}")
    lines.append("")

    lines.append("Extracted:")
    lines.append(f"  {len(session.tracks)} tracks, {len(session.groups)} groups, "
                 f"{len(session.return_tracks)} FX channels"
                 + (", 1 master" if session.master_track else ""))
    lines.append(f"  {len(session.all_devices())} inserts/instruments across tracks")
    lines.append(f"  {len(session.routes)} output-routing edges, "
                 f"{len(session.all_sends())} sends")
    lines.append(f"  {len(session.automation)} automation lanes "
                 f"({sum(a.point_count for a in session.automation)} points)")
    n_notes = sum(c.midi_note_count or 0 for t in session.all_tracks() for c in t.clips)
    lines.append(f"  {n_notes} MIDI notes; {len(session.media)} media refs")
    lines.append("")

    # conflicts
    conflicts = []
    for t in session.all_tracks():
        for f, prov in t.field_provenance.items():
            if getattr(prov, "status", "") == "conflicting":
                conflicts.append(f"  {t.name}.{f}: {prov.explanation}")
    if conflicts:
        lines.append("Conflicts (multiple sources disagreed):")
        lines.extend(conflicts)
        lines.append("")

    lines.append("Unavailable / opaque state:")
    for u in session.unknown_state:
        if u.severity != "info" and u.entity_id is None:
            lines.append(f"  - {u.state_gap}: {u.reason}")
    n_param_gaps = sum(1 for u in session.unknown_state
                       if u.state_gap == "insert_parameter_state")
    if n_param_gaps:
        lines.append(f"  - insert parameter values unavailable for {n_param_gaps} plug-ins "
                     "(opaque state; try preset export / Quick Controls).")
    lines.append("")

    cov = session.metadata.get("coverage") or om.coverage(list(all_arts))
    lines.append(f"Overall state coverage: {cov['coverage_percent']}% "
                 f"of {cov['n_fields']} canonical fields")
    lines.append(f"  revealed:    {len(cov['revealed'])} fields")
    lines.append(f"  constrained: {len(cov['constrained'])} fields (inference only)")
    lines.append(f"  hidden:      {len(cov['hidden'])} fields")
    lines.append("")
    lines.append("Coverage is explainable: it counts revealed fields fully and "
                 "constrained fields at half weight, over the fixed canonical set.")

    if session.warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in session.warnings[:12]:
            lines.append(f"  - {w}")

    return "\n".join(lines)
