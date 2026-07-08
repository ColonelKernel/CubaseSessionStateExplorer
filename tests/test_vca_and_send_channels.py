"""Per-send destination-channel decode + VCA control representation.

Covers the two post-PR-#1 adapter improvements:

1. **Per-send channels** — every Send's ``destination`` IDREF is resolved to
   its exact ``<Channel>``, and that channel's observed ``audioChannels``
   width rides the canonical send route as ``channel_count`` /
   ``channel_layout`` (the Phase-1 routing-depth contract fields). Where the
   width is not stated, the spec stays ``None`` — stereo-implicit, never an
   invented channel layout.
2. **VCA controls** — a ``role="vca"`` channel becomes a control group:
   ``CONTROLS`` edges (``kind=vca_or_edit_group``) to the member channels
   whose faders it scales, and never an output route, ``SUMS_TO``, or any
   other audio path — a VCA sums no signal (grouping honesty, ``_group_sums``).
"""

import os

import pytest

from cubase_session_explorer.extractors import dawproject
from cubase_session_explorer.models import (
    ProjectMeta,
    SendState,
    SessionState,
    TrackState,
)
from cubase_session_explorer.provenance import exported


# ---------------------------------------------------------------------------
# extractor: per-send destination-channel decode
# ---------------------------------------------------------------------------


@pytest.fixture()
def vca_session(fixtures_dir):
    result = dawproject.extract(os.path.join(fixtures_dir, "vca_sends.dawproject"))
    assert result.ok and not result.warnings
    return result.session


def _send_of(session, track_name):
    track = next(t for t in session.all_tracks() if t.name == track_name)
    assert track.sends, f"{track_name} should carry a send"
    return track.sends[0]


def test_send_destination_channel_decoded(vca_session):
    stereo = _send_of(vca_session, "Kick")     # -> ch-fx1, audioChannels=2
    mono = _send_of(vca_session, "Snare")      # -> ch-fx2, audioChannels=1
    assert stereo.destination_channel_id == "ch-fx1"
    assert (stereo.channel_count, stereo.channel_layout) == (2, "stereo")
    assert mono.destination_channel_id == "ch-fx2"
    assert (mono.channel_count, mono.channel_layout) == (1, "mono")
    # the endpoint widths are recorded verbatim in the native side-channel
    assert mono.native["cubase"] == {
        "destination_audio_channels": 1,
        "source_audio_channels": 2,
    }
    # evidence discipline: the width IS read from the XML -> exported, with a
    # locator naming the exact attribute it came from
    prov = stereo.field_provenance["channel_count"]
    assert prov.status == "exported"
    assert prov.source.locator == "Channel[ch-fx1]@audioChannels"


def test_demo_send_gains_channel_spec(fixtures_dir):
    """The pre-existing demo send (real element) decodes its stereo target."""
    result = dawproject.extract(os.path.join(fixtures_dir, "demo_session.dawproject"))
    send = _send_of(result.session, "Lead Vox")
    assert send.destination_channel_id == "ch-fx1"
    assert (send.channel_count, send.channel_layout) == (2, "stereo")


def test_send_without_stated_width_stays_stereo_implicit(tmp_path):
    """No ``audioChannels`` on the destination -> no invented channel spec."""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<Project version="1.0"><Structure>'
        '<Track id="tr-fx" name="FX" contentType="audio">'
        '<Channel id="ch-fx" role="effect"><Volume value="1.0" unit="linear" /></Channel>'
        "</Track>"
        '<Track id="tr-src" name="Src" contentType="audio">'
        '<Channel id="ch-src" audioChannels="2">'
        '<Volume value="1.0" unit="linear" />'
        '<Sends><Send destination="ch-fx" type="post" id="s0" /></Sends>'
        "</Channel></Track>"
        "</Structure></Project>"
    )
    path = tmp_path / "no_width.dawproject"
    path.write_text(xml)
    result = dawproject.extract(str(path))
    send = _send_of(result.session, "Src")
    assert send.destination_channel_id == "ch-fx"
    assert send.channel_count is None
    assert send.channel_layout is None
    assert "channel_count" not in send.field_provenance


# ---------------------------------------------------------------------------
# extractor: VCA channels are control groups, not audio routing
# ---------------------------------------------------------------------------


def test_vca_channel_classified_and_linked(vca_session):
    vca = next(t for t in vca_session.all_tracks() if t.track_type == "vca")
    assert vca.name == "Drum VCA"
    # the mixerRole vocabulary is exact and observed -> role surfaced directly
    assert vca.role == "VCA"
    assert vca.field_provenance["role"].status == "exported"
    kick = next(t for t in vca_session.all_tracks() if t.name == "Kick")
    snare = next(t for t in vca_session.all_tracks() if t.name == "Snare")
    assert set(vca.controls) == {kick.id, snare.id}
    # the IDREF is observed; reading it as control is an interpretation
    controls_prov = vca.field_provenance["controls"]
    assert controls_prov.status == "inferred"
    assert "carries no audio" in controls_prov.explanation
    # members record which VCA channel scales them
    assert kick.native["cubase"]["vca_channel_id"] == "ch-vca"


def test_vca_never_becomes_an_audio_route(vca_session):
    vca = next(t for t in vca_session.all_tracks() if t.track_type == "vca")
    kick = next(t for t in vca_session.all_tracks() if t.name == "Kick")
    # no output route into (or out of) the VCA, and no claimed output target
    assert all(
        vca.id not in (r.source_track_id, r.target_id) for r in vca_session.routes
    )
    assert kick.output_target_id is None
    assert vca.output_target_id is None
    # the FX channels' genuine output routes are untouched
    assert len(vca_session.routes) == 2


# ---------------------------------------------------------------------------
# wire format (requires the shared contract package; sibling-repo policy)
# ---------------------------------------------------------------------------

canonical_snapshot = pytest.importorskip("canonical_snapshot")

from canonical_snapshot import validate_snapshot  # noqa: E402
from cubase_session_explorer.canonical_export import exporter  # noqa: E402
from cubase_session_explorer.canonical_export.mapper import (  # noqa: E402
    session_state_to_canonical,
)


@pytest.fixture()
def vca_bundle(fixtures_dir, tmp_path):
    src = os.path.join(fixtures_dir, "vca_sends.dawproject")
    return exporter.export_bundle(src, str(tmp_path / "bundle"))


def _channel_named(snap, name):
    return next(e for e in snap.entities_of_type("CHANNEL") if e.name == name)


def test_wire_send_routes_carry_channel_spec(vca_bundle):
    snap = vca_bundle.snapshot
    sends = {r.properties["send_name"]: r for r in snap.relationships_of_type("CHANNEL_SENDS_TO")}
    stereo, mono = sends["FX 1"], sends["FX 2"]
    assert stereo.properties["channel_count"] == 2
    assert stereo.properties["channel_layout"] == "stereo"
    assert stereo.properties["destination_channel_id"] == "ch-fx1"
    assert mono.properties["channel_count"] == 1
    assert mono.properties["channel_layout"] == "mono"
    # ...and the sends leave from the exact member channels
    assert stereo.source == _channel_named(snap, "Kick").id
    assert mono.source == _channel_named(snap, "Snare").id


def test_wire_unstated_width_omits_channel_spec(tmp_path):
    """The stereo-implicit send stays spec-less on the wire too."""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<Project version="1.0"><Structure>'
        '<Track id="tr-fx" name="FX" contentType="audio">'
        '<Channel id="ch-fx" role="effect"><Volume value="1.0" unit="linear" /></Channel>'
        "</Track>"
        '<Track id="tr-src" name="Src" contentType="audio">'
        '<Channel id="ch-src" audioChannels="2">'
        '<Volume value="1.0" unit="linear" />'
        '<Sends><Send destination="ch-fx" type="post" id="s0" /></Sends>'
        "</Channel></Track>"
        "</Structure></Project>"
    )
    src = tmp_path / "no_width.dawproject"
    src.write_text(xml)
    result = exporter.export_bundle(str(src), str(tmp_path / "bundle"))
    (send,) = result.snapshot.relationships_of_type("CHANNEL_SENDS_TO")
    assert "channel_count" not in send.properties
    assert "channel_layout" not in send.properties
    assert send.properties["destination_channel_id"] == "ch-fx"


def test_wire_vca_emits_controls_never_sums(vca_bundle):
    snap = vca_bundle.snapshot
    vca_channel = _channel_named(snap, "Drum VCA")
    controls = snap.relationships_of_type("CONTROLS")
    targets = {r.target for r in controls if r.source == vca_channel.id}
    assert targets == {
        _channel_named(snap, "Kick").id,
        _channel_named(snap, "Snare").id,
    }
    assert all(
        r.properties["kind"] == "vca_or_edit_group"
        for r in controls
        if r.source == vca_channel.id
    )
    # grouping honesty: NO audio-summing or signal-flow edge touches the VCA
    for rel_type in ("SUMS_TO", "CHANNEL_ROUTES_TO", "CHANNEL_SENDS_TO"):
        for r in snap.relationships_of_type(rel_type):
            assert vca_channel.id not in (r.source, r.target)


def test_wire_vca_controls_field_is_inferred(vca_bundle):
    snap = vca_bundle.snapshot
    prov_by_id = {p.id: p for p in snap.provenance}
    vca_track = next(
        e for e in snap.entities_of_type("TRACK") if e.name == "Drum VCA"
    )
    controls_prov = prov_by_id[vca_track.prov["controls"]]
    assert controls_prov.evidence == "INFERRED"
    assert "carries no audio" in controls_prov.explanation
    # the bundle as a whole still validates
    report = validate_snapshot(snap.model_dump(mode="json"))
    assert report.valid


# ---------------------------------------------------------------------------
# mapper units: native fields -> nested contract fields
# ---------------------------------------------------------------------------


def test_mapper_vca_track_and_send_channel_fields():
    vca = TrackState(id="cubase:track-vca", name="Bus VCA", track_type="vca",
                     role="VCA", controls=["cubase:track-kick"])
    kick = TrackState(id="cubase:track-kick", name="Kick", track_type="audio")
    kick.sends.append(
        SendState(
            id="cubase:send-1", source_track_id="cubase:track-kick",
            target_return_id="cubase:track-fx", level_db=-12.0,
            destination_channel_id="ch-fx", channel_count=2,
            channel_layout="stereo",
            provenance=exported(source_type="dawproject"),
        )
    )
    state = SessionState(
        project=ProjectMeta(project_name="unit", project_path="/tmp/unit.dawproject"),
        tracks=[vca, kick],
    )
    session = session_state_to_canonical(state)
    nested_vca = session.track_by_id("cubase:track-vca")
    # X06 contract-exhibit convention: kind "unknown" + role "VCA", and the
    # explicit sums_children=False keeps _group_sums from claiming a sum
    assert nested_vca.kind == "unknown"
    assert nested_vca.role == "VCA"
    assert nested_vca.sums_children is False
    assert nested_vca.controls == ["cubase:track-kick"]
    # non-VCA tracks keep the honest default (None: decided by extras/flags)
    assert session.track_by_id("cubase:track-kick").sums_children is None
    (route,) = session.routes
    assert route.channel_count == 2
    assert route.channel_layout == "stereo"
    assert route.extras["destination_channel_id"] == "ch-fx"
