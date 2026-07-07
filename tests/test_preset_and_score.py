"""Tests for the preset lab (.vstpreset) and the MusicXML score-state layer."""

import os
import shutil

from cubase_session_explorer.extractors import musicxml, vstpreset
from cubase_session_explorer.fusion import ingest

DUALFILTER_CLASS_ID = "5C3D6E8F9A0B1C2D3E4F5A6B7C8D9E0F"


# --- preset lab -------------------------------------------------------------

def test_vstpreset_parses_header_chunks_and_meta(fixtures_dir):
    r = vstpreset.extract(os.path.join(fixtures_dir, "dualfilter_a.vstpreset"))
    assert r.ok
    assert r.version == 1
    assert r.class_id == DUALFILTER_CLASS_ID
    assert {c.chunk_id for c in r.chunks} == {"Comp", "Info"}
    # MediaBay attributes from the Info chunk
    assert r.plugin_name == "DualFilter"
    assert r.plugin_category == "Fx|Filter"
    assert r.preset_name == "Dark"
    # Comp state is fingerprinted, never decoded into fabricated values
    comp = r.chunk("Comp")
    assert comp.sha16 and comp.size > 0


def test_vstpreset_diff_detects_controlled_state_change(fixtures_dir):
    d = vstpreset.diff(os.path.join(fixtures_dir, "dualfilter_a.vstpreset"),
                       os.path.join(fixtures_dir, "dualfilter_b.vstpreset"))
    assert d["same_plugin"] is True          # same class id
    assert d["state_changed"] is True        # Comp fingerprint differs
    comp = next(x for x in d["chunk_deltas"] if x["chunk"] == "Comp")
    assert comp["changed"] is True
    info = next(x for x in d["chunk_deltas"] if x["chunk"] == "Info")
    assert info["changed"] is True           # preset name Dark -> Bright


def test_vstpreset_graceful_on_garbage(tmp_path):
    p = tmp_path / "junk.vstpreset"
    p.write_bytes(b"not a preset at all")
    r = vstpreset.extract(str(p))
    assert not r.ok
    assert r.warnings


def test_preset_enriches_matching_device_in_fusion(fixtures_dir, tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for f in ("dualfilter_a.dawproject", "dualfilter_a.vstpreset"):
        shutil.copy(os.path.join(fixtures_dir, f), bundle / f)
    s = ingest(str(bundle)).session
    assert "preset" in s.capture.artifacts
    dev = next(d for d in s.all_devices() if d.name == "DualFilter")
    assert dev.preset_name == "Dark"
    assert dev.field_provenance["preset_name"].source.type == "preset"
    assert dev.native["cubase"]["vstpreset_comp_sha"]


# --- MusicXML score-state ----------------------------------------------------

def test_musicxml_parses_parts_keys_and_spelled_notes(fixtures_dir):
    r = musicxml.extract(os.path.join(fixtures_dir, "score.musicxml"))
    assert r.ok
    s = r.score
    assert s.present
    assert len(s.parts) == 1 and s.parts[0].name == "Lead"
    assert s.parts[0].measure_count == 2
    assert s.key_signatures[0]["fifths"] == -3          # Eb major
    assert s.time_signatures[0] == {"numerator": 4, "denominator": 4,
                                    "measure": 1, "part_id": "P1"}
    spelled = [n.spelled for n in s.pitched_notes]
    assert spelled == ["C4", "Eb4", "F4", "G4", "C5"]
    # spelling and MIDI key are BOTH preserved (representational + performed)
    eb = s.pitched_notes[1]
    assert eb.midi_key == 63


def test_performed_vs_notated_enharmonic_divergence(fixtures_dir, tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for f in ("score_perf.mid", "score.musicxml"):
        shutil.copy(os.path.join(fixtures_dir, f), bundle / f)
    s = ingest(str(bundle)).session
    cmp = s.metadata.get("score_vs_performance")
    assert cmp is not None
    assert cmp["matched"] == 5
    assert cmp["unmatched_midi_keys"] == []
    # the single interpretive act: key 63 notated Eb4, default D#4, inert
    reinterp = cmp["enharmonic_reinterpretations"]
    assert len(reinterp) == 1
    assert reinterp[0]["midi_key"] == 63
    assert reinterp[0]["default_spelling"] == "D#4"
    assert reinterp[0]["score_spelling"] == "Eb4"
    assert reinterp[0]["acoustically_inert"] is True


def test_musicxml_octave_zero_is_not_defaulted(tmp_path):
    # Regression: octave 0 is legal (C0 = MIDI 12); a `... or 4` fallback would
    # corrupt it to C4/60. Also covers alter=0 staying 0.
    xml = ('<?xml version="1.0"?><score-partwise version="4.0"><part-list>'
           '<score-part id="P1"><part-name>Bass</part-name></score-part></part-list>'
           '<part id="P1"><measure number="1"><attributes><divisions>4</divisions>'
           '</attributes>'
           '<note><pitch><step>C</step><octave>0</octave></pitch><duration>4</duration></note>'
           '<note><pitch><step>A</step><octave>0</octave></pitch><duration>4</duration></note>'
           '</measure></part></score-partwise>')
    p = tmp_path / "low.musicxml"
    p.write_text(xml)
    r = musicxml.extract(str(p))
    assert r.ok
    c0, a0 = r.score.pitched_notes
    assert (c0.spelled, c0.midi_key) == ("C0", 12)
    assert (a0.spelled, a0.midi_key) == ("A0", 21)


def test_musicxml_graceful_on_garbage(tmp_path):
    p = tmp_path / "bad.musicxml"
    p.write_text("<oops>")
    r = musicxml.extract(str(p))
    assert not r.ok
    assert r.warnings
