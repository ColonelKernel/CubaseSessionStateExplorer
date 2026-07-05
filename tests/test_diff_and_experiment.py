import os

from cubase_session_explorer.audio_descriptors import descriptor_delta, extract
from cubase_session_explorer.diff import diff_sessions
from cubase_session_explorer.fusion import ingest


def test_dualfilter_parameter_change_detected(fixtures_dir):
    a = ingest(os.path.join(fixtures_dir, "dualfilter_a.dawproject")).session
    b = ingest(os.path.join(fixtures_dir, "dualfilter_b.dawproject")).session
    result = diff_sessions(a, b)
    cats = result.summary()
    assert cats.get("PARAMETER", 0) >= 1
    change = next(c for c in result.changes if c.category == "PARAMETER")
    assert "DualFilter/Position" in change.target
    assert change.before == 0.25
    assert change.after == 0.75


def test_routing_change_detected(fixtures_dir):
    a = ingest(os.path.join(fixtures_dir, "routing_a.dawproject")).session
    b = ingest(os.path.join(fixtures_dir, "routing_b.dawproject")).session
    result = diff_sessions(a, b)
    cats = result.summary()
    assert cats.get("ROUTING", 0) >= 1
    assert cats.get("STRUCTURAL", 0) >= 1  # new FX channel node
    assert len(a.all_sends()) == 0
    assert len(b.all_sends()) == 1


def test_audio_delta_measurable(fixtures_dir):
    da = extract(os.path.join(fixtures_dir, "dualfilter_a.wav"))
    db = extract(os.path.join(fixtures_dir, "dualfilter_b.wav"))
    assert da.available and db.available
    delta = descriptor_delta(da, db)
    # some acoustic descriptor must actually change
    assert any(abs(v["delta"]) > 1e-4 for v in delta.values())


def test_opaque_vst3_state_change_detected_honestly(fixtures_dir):
    a = ingest(os.path.join(fixtures_dir, "opaque_a.dawproject")).session
    b = ingest(os.path.join(fixtures_dir, "opaque_b.dawproject")).session
    # the plug-in parameter value is NOT readable / not fabricated
    dev = next(iter(a.all_devices()))
    assert dev.parameters == []
    assert dev.field_provenance["parameters"].status == "unavailable"
    # ...but the controlled change IS detectable, honestly, as UNKNOWN
    result = diff_sessions(a, b)
    assert result.summary().get("UNKNOWN", 0) >= 1
    change = next(c for c in result.changes if c.category == "UNKNOWN")
    assert "opaque" in change.target.lower() or "opaque" in change.detail.lower()
    assert change.before != change.after  # blob fingerprints differ


def test_identical_sessions_no_changes(fixtures_dir):
    a = ingest(os.path.join(fixtures_dir, "routing_a.dawproject")).session
    b = ingest(os.path.join(fixtures_dir, "routing_a.dawproject")).session
    assert diff_sessions(a, b).changes == []
