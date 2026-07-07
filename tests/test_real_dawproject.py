"""Validation against a real-format DAWproject document.

The <Track>/<Channel>/<Devices>/<Notes> below is the genuine Bitwig Studio 5.0
export embedded in the official bitwig/dawproject README (the closest public
proxy for real Cubase output — Steinberg ships no sample XML). It is augmented
with a spec-correct standalone master <Channel>, a <Send> (with <Enable>), and a
<Points> automation lane with a <Target parameter> IDREF, so every hardened
parser path is exercised against real-format XML rather than our own fixtures.

Key real-format traits asserted: normalized note velocity (0.787402 -> 100),
linear volume (0.659140 -> dB), normalized pan center 0.5 -> 0.0, IDREF routing
via `destination`, standalone master Channel in <Structure>, and automation
Target resolution.
"""

import io
import os
import zipfile

from cubase_session_explorer.extractors import dawproject

REAL_PROJECT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Project version="1.0">
  <Application name="Bitwig Studio" version="5.0"/>
  <Transport>
    <Tempo unit="bpm" value="149.000000" id="id0"/>
    <TimeSignature numerator="4" denominator="4" id="id1"/>
  </Transport>
  <Structure>
    <Track contentType="notes" loaded="true" id="id2" name="Bass" color="#a2eabf">
      <Channel audioChannels="2" destination="id15" role="regular" solo="false" id="id3">
        <Devices>
          <ClapPlugin deviceID="org.surge-synth-team.surge-xt" deviceName="Surge XT"
                      deviceRole="instrument" loaded="true" id="id7" name="Surge XT">
            <Parameters/>
            <Enabled value="true" id="id8" name="On/Off"/>
            <State path="plugins/surge.clap-preset"/>
          </ClapPlugin>
        </Devices>
        <Sends>
          <Send destination="id15" type="post" id="id40" name="Master Send">
            <Enable value="true" id="id41" name="On/Off"/>
            <Volume max="2.0" min="0.0" unit="linear" value="0.350000" id="id43"/>
          </Send>
        </Sends>
        <Mute value="false" id="id6" name="Mute"/>
        <Pan max="1.000000" min="0.000000" unit="normalized" value="0.500000" id="id5" name="Pan"/>
        <Volume max="2.000000" min="0.000000" unit="linear" value="0.659140" id="id4" name="Volume"/>
      </Channel>
    </Track>
    <Channel role="master" audioChannels="2" id="id15" name="Master">
      <Volume unit="linear" value="1.000000" id="id16"/>
      <Pan unit="normalized" value="0.500000" id="id17"/>
    </Channel>
  </Structure>
  <Arrangement id="id19">
    <Lanes timeUnit="beats" id="id20">
      <Lanes track="id2" id="id21">
        <Clips id="id22">
          <Clip time="0.0" duration="8.0" playStart="0.0">
            <Notes id="id23">
              <Note time="0.000000" duration="0.250000" channel="0" key="65" vel="0.787402" rel="0.787402"/>
              <Note time="1.500000" duration="2.500000" channel="0" key="53" vel="0.787402" rel="0.787402"/>
              <Note time="6.000000" duration="2.000000" channel="0" key="53" vel="0.787402" rel="0.787402"/>
            </Notes>
          </Clip>
        </Clips>
      </Lanes>
      <Lanes track="id2" id="id30">
        <Points unit="linear" timeUnit="beats" id="id50">
          <Target parameter="id4"/>
          <RealPoint time="0.0" value="0.659140" interpolation="linear"/>
          <RealPoint time="4.0" value="1.000000" interpolation="hold"/>
          <RealPoint time="8.0" value="0.500000"/>
        </Points>
      </Lanes>
    </Lanes>
  </Arrangement>
</Project>
"""


def _write(tmp_path):
    p = os.path.join(str(tmp_path), "real.dawproject")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("project.xml", REAL_PROJECT_XML)
        z.writestr("metadata.xml", "<?xml version='1.0'?><MetaData/>")
    with open(p, "wb") as fh:
        fh.write(buf.getvalue())
    return p


def test_real_format_parses_fully(tmp_path):
    res = dawproject.extract(_write(tmp_path))
    assert res.ok
    s = res.session
    # every element localname is handled (no hardening gaps on real-format XML)
    census = dawproject.element_census(_write(tmp_path))
    assert census["unhandled_elements"] == {}

    # transport
    assert s.tempo == 149.0
    assert s.time_signature == "4/4"

    # standalone master Channel in <Structure> is recovered
    assert s.master_track is not None
    assert s.master_track.name == "Master"

    # Bass track: notes content + instrument device
    bass = next(t for t in s.all_tracks() if t.name == "Bass")
    assert bass.native["cubase"]["is_instrument_track"] is True
    surge = bass.devices[0]
    assert surge.name == "Surge XT" and surge.plugin_format == "internal"
    assert surge.device_type == "instrument"
    assert surge.parameters == []  # opaque State blob
    assert surge.field_provenance["parameters"].status == "unavailable"


def test_real_format_value_conventions(tmp_path):
    s = dawproject.extract(_write(tmp_path)).session
    bass = next(t for t in s.all_tracks() if t.name == "Bass")
    # linear volume 0.659140 -> dB (not assumed already-dB)
    assert bass.volume_db is not None and -4.0 < bass.volume_db < -3.0
    # normalized pan center 0.5 -> 0.0
    assert abs(bass.pan) < 1e-6
    # normalized velocity 0.787402 -> ~100 MIDI (NOT 0.78, NOT 787)
    note = bass.clips[0].notes[0]
    assert note.key == 65
    assert note.velocity == 100
    assert note.release_velocity == 100


def test_real_format_routing_send_and_automation(tmp_path):
    s = dawproject.extract(_write(tmp_path)).session
    bass = next(t for t in s.all_tracks() if t.name == "Bass")
    # IDREF routing: Bass -> master
    assert bass.output_target_id == s.master_track.id
    # Send with <Enable> child + linear <Volume>
    assert len(bass.sends) == 1
    send = bass.sends[0]
    assert send.enabled is True
    assert send.level_db is not None
    # automation Target parameter="id4" resolves to the channel Volume
    vol_lane = [a for a in s.automation if a.parameter_name == "Volume"]
    assert len(vol_lane) == 1
    assert vol_lane[0].point_count == 3
    assert vol_lane[0].points[1].curve == "step"  # interpolation="hold"
