import os

from cubase_session_explorer.extractors import cpr_lab, midi
from cubase_session_explorer.fusion import ingest
from cubase_session_explorer.graph_builder import build_graph_dict
from cubase_session_explorer.snapshot import load_snapshot, save_snapshot


def test_midi_extractor(fixtures_dir):
    res = midi.extract(os.path.join(fixtures_dir, "notes.mid"))
    assert res.ok
    assert res.tempo_bpm == 120.0
    notes = [n for t in res.tracks for n in t.notes]
    assert len(notes) == 4
    assert notes[0].key == 60


def test_cpr_lab_on_nonexistent_is_graceful(tmp_path):
    rep = cpr_lab.scan(str(tmp_path / "missing.cpr"))
    assert not rep.is_probable_cpr
    assert rep.warnings


def test_cpr_lab_detects_tokens(tmp_path):
    # Synthesize a minimal RIFF-ish blob with Cubase tokens + a plug-in name.
    blob = b"RIFF????NUNDROOT....CmObject...DualFilter\x00\x00StudioEQ..PAppVersion 15.0"
    p = tmp_path / "fake.cpr"
    p.write_bytes(blob)
    rep = cpr_lab.scan(str(p))
    assert rep.is_probable_cpr
    names = [e.value for e in rep.plugin_name_candidates]
    assert any("DualFilter" in n for n in names)


def test_bundle_ingest_missing_path_is_graceful(tmp_path):
    res = ingest(str(tmp_path / "nope"))
    assert res.session is not None  # evidence-only session, not a crash


def test_snapshot_roundtrip(fixtures_dir, tmp_path):
    s = ingest(os.path.join(fixtures_dir, "demo_session.dawproject")).session
    out = str(tmp_path / "snap.json")
    save_snapshot(s, out)
    s2 = load_snapshot(out)
    assert s2.project.project_name == s.project.project_name
    assert len(s2.all_tracks()) == len(s.all_tracks())


def test_graph_builds_without_networkx(fixtures_dir):
    s = ingest(os.path.join(fixtures_dir, "demo_session.dawproject")).session
    g = build_graph_dict(s)
    assert g["metadata"]["n_nodes"] > 10
    types = {n["type"] for n in g["nodes"]}
    assert {"project", "track", "device", "unknown_state"} <= types
    # the send is an edge, not a containment
    assert any(e["type"] == "SENDS_TO" for e in g["edges"])


def test_track_archive_and_dawproject_bundle(fixtures_dir, tmp_path):
    # A DAWproject alongside a runtime snapshot should fuse cleanly.
    import json
    d = tmp_path / "bundle"
    d.mkdir()
    import shutil
    shutil.copy(os.path.join(fixtures_dir, "demo_session.dawproject"),
                d / "demo_session.dawproject")
    (d / "runtime.json").write_text(json.dumps({
        "cubase_version": "15.0.10",
        "channels": [{"name": "Lead Vox", "volume_db": -1.0, "mute": True, "selected": True}],
    }))
    res = ingest(str(d))
    vox = next(t for t in res.session.all_tracks() if t.name == "Lead Vox")
    # runtime observation upgrades the mute field to observed ground truth
    assert vox.mute is True
    assert vox.field_provenance["mute"].source.type == "midi_remote"
