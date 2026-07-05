"""Fixture-driven validation of the DAWproject extractor.

Each assertion pairs an EXPECTED value (from the fixture generator's intent)
with the OBSERVED extraction, per the validation strategy in docs/VALIDATION.md.
"""

import os

from cubase_session_explorer.fusion import ingest


def _session(fixtures_dir, name):
    return ingest(os.path.join(fixtures_dir, name)).session


def test_demo_session_structure(fixtures_dir):
    s = _session(fixtures_dir, "demo_session.dawproject")
    # EXPECTED tempo/timesig
    assert s.tempo == 120.0
    assert s.time_signature == "4/4"
    # EXPECTED >= 4 audio tracks, a group, an fx channel, a master
    assert len(s.tracks) >= 4
    assert any(t.track_type == "group" for t in s.all_tracks())
    assert len(s.return_tracks) == 1
    assert s.master_track is not None
    assert s.project.cubase_version == "15.0.10"


def test_demo_session_devices_and_families(fixtures_dir):
    s = _session(fixtures_dir, "demo_session.dawproject")
    names = {d.name for d in s.all_devices()}
    assert {"StudioEQ", "DeEsser", "Retrologue", "REVerence"} <= names
    # backfilled families are inferred, not asserted as facts
    vox = next(t for t in s.tracks if t.name == "Lead Vox")
    eq = next(d for d in vox.devices if d.name == "StudioEQ")
    assert eq.device_family == "EQ"
    assert eq.field_provenance["device_family"].status == "inferred"


def test_instrument_track_has_notes(fixtures_dir):
    s = _session(fixtures_dir, "demo_session.dawproject")
    pad = next(t for t in s.all_tracks() if t.name == "Synth Pad")
    midi_clip = next(c for c in pad.clips if c.clip_type == "midi")
    assert midi_clip.midi_note_count == 4
    assert midi_clip.notes[0].key == 60


def test_routing_and_sends(fixtures_dir):
    s = _session(fixtures_dir, "demo_session.dawproject")
    # vox sends to the fx channel
    sends = s.all_sends()
    assert len(sends) == 1
    fx = s.return_tracks[0]
    assert sends[0].target_return_id == fx.id
    # kick/snare route into the group
    group = next(t for t in s.all_tracks() if t.track_type == "group")
    inbound = [r for r in s.routes if r.target_id == group.id]
    assert len(inbound) >= 2


def test_automation_lane(fixtures_dir):
    s = _session(fixtures_dir, "demo_session.dawproject")
    assert len(s.automation) == 1
    assert s.automation[0].point_count == 3


def test_builtin_params_readable_vst3_params_unavailable(fixtures_dir):
    s = _session(fixtures_dir, "demo_session.dawproject")
    # Built-in devices (Equalizer/Compressor) expose REAL parameter values...
    kick = next(t for t in s.all_tracks() if t.name == "Kick")
    eq = next(d for d in kick.devices if d.name == "Frequency")
    assert eq.device_family == "EQ"
    assert any(p.name == "Low Gain" and p.value == 0.6 for p in eq.parameters)
    assert eq.parameters[0].provenance.status == "exported"  # observed, not guessed
    # ...while an opaque VST3 (State blob, no enumerable params) is NOT fabricated
    # and is explicitly flagged unavailable.
    vox = next(t for t in s.all_tracks() if t.name == "Lead Vox")
    opaque = next(d for d in vox.devices if d.name == "StudioEQ")
    assert opaque.parameters == []
    assert opaque.field_provenance["parameters"].status == "unavailable"
    # The gap is a first-class object, not silence.
    assert any(u.state_gap == "insert_parameter_state" for u in s.unknown_state)


def test_coverage_is_reported_and_bounded(fixtures_dir):
    s = _session(fixtures_dir, "demo_session.dawproject")
    cov = s.capture.coverage_percent
    assert 0 < cov < 100  # partial observability, honestly


def test_provenance_is_attached(fixtures_dir):
    s = _session(fixtures_dir, "demo_session.dawproject")
    vox = next(t for t in s.tracks if t.name == "Lead Vox")
    assert vox.provenance.status == "exported"
    assert vox.provenance.source.type == "dawproject"
