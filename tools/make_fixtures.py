"""Generate controlled Cubase-compatible fixtures.

DAWproject is an open MIT format that Cubase 14/15 import and export, so we can
author *valid* ``.dawproject`` fixtures directly and parse real state from them
— no Cubase install required for the pipeline to run end-to-end. We also
synthesize matching WAV renders (stdlib ``wave``, no numpy) so the state->audio
intervention demo produces a genuine, measurable acoustic delta.

Run:  python tools/make_fixtures.py [output_dir]

Fixtures produced (subset of docs/CUBASE_FIXTURE_PROTOCOL.md):
  demo_session.dawproject        full multi-track session (graph/UI demo)
  dualfilter_a/b.dawproject      headline A/B: DualFilter Position -0.5 -> +0.5
  dualfilter_a/b.wav             matching renders (low-pass vs high-pass tone)
  routing_a/b.dawproject         dry vocal  vs  vocal + reverb send
  routing_a/b.wav                matching renders (dry vs reverb tail)
  vca_sends.dawproject           vca-role channel + mono/stereo send targets
  notes.mid                      a short MIDI performance
  manifest.json                  expected-value manifest for validation
"""

from __future__ import annotations

import math
import os
import struct
import sys
import wave
import xml.etree.ElementTree as ET
import zipfile


# --------------------------------------------------------------------------
# DAWproject XML builder (minimal, faithful to bitwig/dawproject structure)
# --------------------------------------------------------------------------

def _sub(parent, tag, **attrs):
    el = ET.SubElement(parent, tag)
    for k, v in attrs.items():
        if v is not None:
            el.set(k, str(v))
    return el


def _real(parent, tag, value, unit="linear", pid=None):
    el = _sub(parent, tag, value=value, unit=unit, id=pid)
    return el


def build_project(app_version="15.0.10"):
    root = ET.Element("Project", version="1.0")
    _sub(root, "Application", name="Cubase", version=app_version)
    transport = _sub(root, "Transport")
    _real(transport, "Tempo", 120.0, unit="bpm", pid="tempo")
    _sub(transport, "TimeSignature", numerator=4, denominator=4, id="tsig")
    structure = _sub(root, "Structure")
    arrangement = _sub(root, "Arrangement", id="arr")
    lanes = _sub(arrangement, "Lanes", timeUnit="beats", id="arrlanes")
    return root, structure, arrangement, lanes


def add_master(structure, name="Stereo Out", ch_id="ch-master"):
    tr = _sub(structure, "Track", id="tr-master", name=name, contentType="audio",
              loaded="true")
    ch = _sub(tr, "Channel", id=ch_id, role="master", audioChannels="2")
    _real(ch, "Volume", 1.0, unit="linear")
    _real(ch, "Pan", 0.5, unit="normalized")
    return ch_id


def add_track(structure, name, ch_id, destination, content="audio",
              volume=0.85, pan=0.5, role=None, color=None, devices=None,
              sends=None, audio_channels="2"):
    tr = _sub(structure, "Track", id=f"tr-{ch_id}", name=name,
              contentType=content, loaded="true", color=color)
    ch = _sub(tr, "Channel", id=ch_id, destination=destination,
              audioChannels=audio_channels, role=role)
    _real(ch, "Volume", volume, unit="linear", pid=f"{ch_id}-vol")
    _real(ch, "Pan", pan, unit="normalized", pid=f"{ch_id}-pan")
    _sub(ch, "Mute", value="false", id=f"{ch_id}-mute")
    if devices:
        devs = _sub(ch, "Devices")
        for i, d in enumerate(devices):
            _emit_device(devs, d, ch_id, i)
    if sends:
        sends_el = _sub(ch, "Sends")
        for j, s in enumerate(sends):
            se = _sub(sends_el, "Send", destination=s["destination"],
                      type=s.get("type", "post"), id=f"send-{ch_id}-{j}",
                      name=s.get("name"))
            _sub(se, "Enable", value="true", id=f"send-{ch_id}-{j}-en")
            _real(se, "Volume", s.get("level", 0.3), unit="linear",
                  pid=f"send-{ch_id}-{j}-vol")
    return f"tr-{ch_id}", ch_id


def _emit_device(devs, d, ch_id, i):
    """Emit one device. ``element`` picks Vst3Plugin/Equalizer/etc.; ``params``
    (name->value) emits enumerable <Parameters> (readable); otherwise a <State>
    blob is written (opaque, like a real Cubase VST3)."""
    el = _sub(devs, d.get("element", "Vst3Plugin"),
              deviceName=d["name"], deviceID=d.get("id", d["name"]),
              deviceRole=d.get("role", "audioFX"),
              id=f"dev-{ch_id}-{i}", name=d["name"])
    _sub(el, "Enabled", value="true" if d.get("enabled", True) else "false",
         id=f"dev-{ch_id}-{i}-en")
    params = d.get("params")
    if params:
        pel = _sub(el, "Parameters")
        for pname, pval in params.items():
            _sub(pel, "RealParameter", name=pname, unit="normalized",
                 value=f"{pval:.6f}", id=f"dev-{ch_id}-{i}-{pname}".replace(" ", ""))
    else:
        _sub(el, "State", path=f"plugins/{d['name']}-{ch_id}.vstpreset")


def add_clip(lanes, track_ref, time, duration, name, notes=None, audio=None):
    tl = _sub(lanes, "Lanes", track=track_ref, id=f"lane-{track_ref}")
    clips = _sub(tl, "Clips", id=f"clips-{track_ref}")
    clip = _sub(clips, "Clip", time=time, duration=duration, name=name)
    if notes:
        notes_el = _sub(clip, "Notes")
        for (t, dur, key, vel) in notes:
            _sub(notes_el, "Note", time=t, duration=dur, key=key, vel=vel)
    if audio:
        warps = _sub(clip, "Warps", contentTimeUnit="beats")
        au = _sub(warps, "Audio", algorithm="stretch", channels="2",
                  duration=duration, sampleRate="44100", id=f"au-{track_ref}")
        _sub(au, "File", path=audio)
    return tl


def add_automation(lanes, track_ref, target_param_id, points, unit="linear"):
    tl = _sub(lanes, "Lanes", track=track_ref, id=f"autolane-{track_ref}")
    pts = _sub(tl, "Points", unit=unit, timeUnit="beats",
               id=f"pts-{track_ref}-{target_param_id}")
    _sub(pts, "Target", parameter=target_param_id)   # IDREF to a Parameter's id
    for (t, v) in points:
        _sub(pts, "RealPoint", time=t, value=f"{v:.6f}", interpolation="linear")
    return tl


def write_dawproject(root, path, extra_files=None):
    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    meta = b'<?xml version="1.0" encoding="UTF-8"?>\n<MetaData/>\n'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.xml", xml_bytes)
        zf.writestr("metadata.xml", meta)
        for name, data in (extra_files or {}).items():
            zf.writestr(name, data)


# --------------------------------------------------------------------------
# Audio render synthesis (stdlib wave; no numpy)
# --------------------------------------------------------------------------

def _write_wav(path, samples, sr=44100):
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for s in samples:
            v = int(max(-1.0, min(1.0, s)) * 30000)
            frames += struct.pack("<h", v)
        w.writeframes(bytes(frames))


def _one_pole(samples, alpha, highpass=False):
    out = []
    prev = 0.0
    prev_in = 0.0
    for x in samples:
        lp = prev + alpha * (x - prev)
        prev = lp
        if highpass:
            out.append(x - lp)
        else:
            out.append(lp)
        prev_in = x
    return out


def render_dualfilter(path, position, sr=44100, dur=2.0):
    """A harmonically rich tone filtered by 'position': -1 dark .. +1 bright."""
    n = int(sr * dur)
    base = []
    for i in range(n):
        t = i / sr
        # sawtooth-ish sum of harmonics
        s = sum((1.0 / k) * math.sin(2 * math.pi * 110 * k * t) for k in range(1, 12))
        base.append(0.2 * s)
    # position in [-1,1]; map to a lowpass/highpass blend.
    p = max(-1.0, min(1.0, position))
    lp = _one_pole(base, alpha=0.05 + 0.4 * (p + 1) / 2)  # brighter as p rises
    hp = _one_pole(base, alpha=0.2, highpass=True)
    mix = (1 - (p + 1) / 2)
    out = [lp[i] * (1 - mix) + hp[i] * mix + lp[i] * 0.2 for i in range(n)]
    _write_wav(path, out, sr)


def render_routing(path, with_reverb, sr=44100, dur=2.0):
    """A vocal-like tone; the 'with reverb' render adds a decaying tail."""
    n = int(sr * dur)
    dry = []
    for i in range(n):
        t = i / sr
        env = math.exp(-3 * (t % 1.0))
        s = env * (math.sin(2 * math.pi * 220 * t) + 0.4 * math.sin(2 * math.pi * 440 * t))
        dry.append(0.25 * s)
    if not with_reverb:
        _write_wav(path, dry, sr)
        return
    # crude feedback-comb reverb -> longer, denser tail
    out = list(dry)
    for delay_s, gain in ((0.037, 0.5), (0.053, 0.4), (0.071, 0.35), (0.11, 0.3)):
        d = int(delay_s * sr)
        for i in range(d, n):
            out[i] += gain * out[i - d]
    peak = max(1e-6, max(abs(x) for x in out))
    out = [x / peak * 0.8 for x in out]
    _write_wav(path, out, sr)


# --------------------------------------------------------------------------
# VST3 preset (.vstpreset) — spec-exact per the official SDK format:
# 'VST3' + int32 version + 32-byte ASCII class id + int64 chunk-list offset;
# data area; 'List' + int32 count + entries of (id, int64 offset, int64 size).
# --------------------------------------------------------------------------

def write_vstpreset(path, class_id, plugin_name, preset_name, comp_payload):
    assert len(class_id) == 32
    info_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<MetaInfo>\n"
        f'  <Attribute id="PlugInName" value="{plugin_name}" type="string"/>\n'
        f'  <Attribute id="PlugInCategory" value="Fx|Filter" type="string"/>\n'
        f'  <Attribute id="Name" value="{preset_name}" type="string"/>\n'
        "</MetaInfo>\n"
    ).encode()
    header_size = 4 + 4 + 32 + 8
    comp_off = header_size
    info_off = comp_off + len(comp_payload)
    list_off = info_off + len(info_xml)
    with open(path, "wb") as f:
        f.write(struct.pack("<4si32sq", b"VST3", 1, class_id.encode(), list_off))
        f.write(comp_payload)
        f.write(info_xml)
        f.write(b"List" + struct.pack("<i", 2))
        f.write(struct.pack("<4sqq", b"Comp", comp_off, len(comp_payload)))
        f.write(struct.pack("<4sqq", b"Info", info_off, len(info_xml)))


DUALFILTER_CLASS_ID = "5C3D6E8F9A0B1C2D3E4F5A6B7C8D9E0F"  # fixture FUID


# --------------------------------------------------------------------------
# MusicXML — a notated interpretation of a performed MIDI passage.
# The score spells MIDI key 63 as Eb4 (flat-side, key of Eb major) where a
# piano-roll default would say D#4: a purely REPRESENTATIONAL divergence.
# --------------------------------------------------------------------------

def write_musicxml(path):
    notes = [("C", 0, 4), ("E", -1, 4), ("F", 0, 4), ("G", 0, 4), ("C", 0, 5)]
    body = []
    for step, alter, octave in notes:
        alter_el = f"<alter>{alter}</alter>" if alter else ""
        body.append(
            "      <note><pitch>"
            f"<step>{step}</step>{alter_el}<octave>{octave}</octave>"
            "</pitch><duration>4</duration><voice>1</voice><type>quarter</type></note>"
        )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list>
    <score-part id="P1">
      <part-name>Lead</part-name>
      <score-instrument id="P1-I1"><instrument-name>Synth Lead</instrument-name></score-instrument>
    </score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>4</divisions>
        <key><fifths>-3</fifths><mode>major</mode></key>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
{chr(10).join(body[:4])}
    </measure>
    <measure number="2">
{body[4]}
    </measure>
  </part>
</score-partwise>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)


# --------------------------------------------------------------------------
# MIDI file (format 1, one track)
# --------------------------------------------------------------------------

def write_midi(path, notes, division=480, name="Synth Pad"):
    def vlq(n):
        buf = bytearray([n & 0x7F])
        n >>= 7
        while n:
            buf.insert(0, (n & 0x7F) | 0x80)
            n >>= 7
        return bytes(buf)

    track = bytearray()
    track += b"\x00" + b"\xFF\x03" + vlq(len(name)) + name.encode()
    track += b"\x00" + b"\xFF\x51\x03" + struct.pack(">I", 500000)[1:]  # 120bpm
    events = []
    for (start, dur, key, vel) in notes:
        events.append((int(start * division), 0x90, key, vel))
        events.append((int((start + dur) * division), 0x80, key, 0))
    events.sort(key=lambda e: e[0])
    prev = 0
    for (tick, status, key, vel) in events:
        track += vlq(tick - prev) + bytes([status, key, vel])
        prev = tick
    track += b"\x00\xFF\x2F\x00"
    header = b"MThd" + struct.pack(">IHHH", 6, 1, 1, division)
    chunk = b"MTrk" + struct.pack(">I", len(track)) + bytes(track)
    with open(path, "wb") as f:
        f.write(header + chunk)


# --------------------------------------------------------------------------
# Fixture assembly
# --------------------------------------------------------------------------

def make_demo_session(path):
    root, structure, arr, lanes = build_project()
    master = add_master(structure)
    # FX channel: real DAWproject mixerRole is 'effect' (not 'effectTrack').
    add_track(structure, "FX 1 - Plate", "ch-fx1", master, role="effect",
              devices=[{"name": "REVerence", "role": "audioFX"}])
    # Group bus: role 'submix'. Children route into it.
    add_track(structure, "Drum Bus", "ch-grp", master, role="submix", volume=0.8)
    # Built-in EQ emitted as <Equalizer> with enumerable Parameters (readable).
    add_track(structure, "Kick", "ch-kick", "ch-grp", color="#C44536",
              devices=[{"name": "Frequency", "element": "Equalizer",
                        "params": {"Low Gain": 0.6}},
                       {"name": "Compressor", "element": "Compressor",
                        "params": {"Threshold": 0.4}}])
    add_track(structure, "Snare", "ch-snare", "ch-grp",
              devices=[{"name": "Frequency", "element": "Equalizer"}])
    # Vox: a VST3 with an opaque <State> blob (params unavailable, honestly).
    tr_vox, _ = add_track(
        structure, "Lead Vox", "ch-vox", master, color="#D4A017", volume=0.9,
        devices=[{"name": "StudioEQ"}, {"name": "DeEsser"}, {"name": "Tube Compressor"}],
        sends=[{"destination": "ch-fx1", "level": 0.25, "name": "FX 1"}])
    tr_pad, _ = add_track(structure, "Synth Pad", "ch-pad", master, content="notes",
                          devices=[{"name": "Retrologue", "role": "instrument",
                                    "element": "Vst3Plugin"}])
    add_clip(lanes, "ch-vox", 0.0, 16.0, "Lead Vox Comp", audio="audio/vox.wav")
    add_clip(lanes, "ch-pad", 0.0, 16.0, "Pad Part",
             notes=[(0, 4, 60, 0.7), (4, 4, 64, 0.7), (8, 4, 67, 0.7), (12, 4, 72, 0.7)])
    # Automation lane targets the vox channel Volume parameter by IDREF.
    add_automation(lanes, "ch-vox", "ch-vox-vol",
                   [(0.0, 0.6), (8.0, 0.8), (16.0, 0.7)])
    write_dawproject(root, path)


def make_dualfilter(path, position):
    root, structure, arr, lanes = build_project()
    master = add_master(structure)
    # DualFilter Position exposed as a readable device RealParameter (normalized).
    norm = round((position + 1.0) / 2.0, 4)  # -1..1 -> 0..1
    add_track(structure, "Gtr Wide", "ch-gtr", master, color="#2E86AB",
              devices=[{"name": "DualFilter", "role": "audioFX",
                        "params": {"Position": norm}}])
    add_clip(lanes, "ch-gtr", 0.0, 8.0, "Gtr L+R", audio="audio/gtr.wav")
    write_dawproject(root, path)


def make_folder_group(path):
    """P09: a folder track whose group channel is enabled, with children routed
    into it (folder-vs-group distinction, nested <Track>, standalone-ish bus)."""
    root, structure, arr, lanes = build_project()
    master = add_master(structure)
    # Folder track WITH a group channel (contentType 'tracks', has <Channel>).
    folder_tr = _sub(structure, "Track", id="tr-folder", name="Drums (Folder)",
                     contentType="tracks", loaded="true", color="#C44536")
    fch = _sub(folder_tr, "Channel", id="ch-folder", destination=master,
               role="submix", audioChannels="2")
    _real(fch, "Volume", 0.8, unit="linear", pid="ch-folder-vol")
    _real(fch, "Pan", 0.5, unit="normalized")
    _sub(fch, "Mute", value="false")
    # Children nested INSIDE the folder track, routed to the folder's group channel.
    add_track(folder_tr, "Kick", "ch-kick", "ch-folder",
              devices=[{"name": "Frequency", "element": "Equalizer"}])
    add_track(folder_tr, "Snare", "ch-snare", "ch-folder")
    add_clip(lanes, "ch-kick", 0.0, 8.0, "Kick", audio="audio/kick.wav")
    write_dawproject(root, path)


def make_vca_sends(path):
    """P6 routing depth: a vca-role channel whose members reference it by
    ``destination`` IDREF (level control, never an audio sum), plus sends whose
    destination <Channel>s state their width (stereo FX 1 vs mono FX 2) so the
    per-send channel spec is genuinely observable."""
    root, structure, arr, lanes = build_project()
    master = add_master(structure)
    add_track(structure, "FX 1 - Plate", "ch-fx1", master, role="effect",
              devices=[{"name": "REVerence"}])
    add_track(structure, "FX 2 - Slap", "ch-fx2", master, role="effect",
              audio_channels="1", devices=[{"name": "MonoDelay"}])
    # VCA fader: role 'vca', a Volume fader, no destination and no audio
    # width — a VCA carries no signal, so stating either would be invention.
    vca_tr = _sub(structure, "Track", id="tr-vca", name="Drum VCA",
                  contentType="audio", loaded="true", color="#7A6FBE")
    vch = _sub(vca_tr, "Channel", id="ch-vca", role="vca")
    _real(vch, "Volume", 0.9, unit="linear", pid="ch-vca-vol")
    # Members: their destination IDREFs resolve to the VCA channel.
    add_track(structure, "Kick", "ch-kick", "ch-vca",
              sends=[{"destination": "ch-fx1", "level": 0.3, "name": "FX 1"}])
    add_track(structure, "Snare", "ch-snare", "ch-vca",
              sends=[{"destination": "ch-fx2", "level": 0.2, "name": "FX 2"}])
    add_clip(lanes, "ch-kick", 0.0, 8.0, "Kick", audio="audio/kick.wav")
    write_dawproject(root, path)


def make_opaque(path, position):
    """Opaque VST3: DualFilter as a Vst3Plugin with a <State> blob whose BYTES
    encode the setting. Values are not readable, but the blob hash makes a
    controlled change detectable (the honest Cubase-VST3 case)."""
    root, structure, arr, lanes = build_project()
    master = add_master(structure)
    tr = _sub(structure, "Track", id="tr-gtr", name="Gtr Wide",
              contentType="audio", loaded="true", color="#2E86AB")
    ch = _sub(tr, "Channel", id="ch-gtr", destination=master, audioChannels="2")
    _real(ch, "Volume", 0.85, unit="linear", pid="ch-gtr-vol")
    _real(ch, "Pan", 0.5, unit="normalized")
    devs = _sub(ch, "Devices")
    el = _sub(devs, "Vst3Plugin", deviceName="DualFilter", deviceID="DualFilter",
              deviceRole="audioFX", id="dev-gtr-0", name="DualFilter")
    _sub(el, "Enabled", value="true", id="dev-gtr-0-en")
    _sub(el, "State", path="plugins/dualfilter.vstpreset")  # opaque blob
    add_clip(lanes, "ch-gtr", 0.0, 8.0, "Gtr L+R", audio="audio/gtr.wav")
    # The blob bytes differ by position -> different hash -> detectable change.
    blob = b"VST3PRESET\x00DualFilter\x00Position=" + f"{position:+.3f}".encode()
    write_dawproject(root, path, extra_files={"plugins/dualfilter.vstpreset": blob})


def make_routing(path, with_send):
    root, structure, arr, lanes = build_project()
    master = add_master(structure)
    if with_send:
        add_track(structure, "FX 1 - Plate", "ch-fx1", master, role="effect",
                  devices=[{"name": "REVerence"}])
    sends = [{"destination": "ch-fx1", "level": 0.4, "name": "FX 1"}] if with_send else None
    add_track(structure, "Lead Vox", "ch-vox", master, color="#D4A017",
              devices=[{"name": "StudioEQ"}], sends=sends)
    add_clip(lanes, "ch-vox", 0.0, 8.0, "Lead Vox", audio="audio/vox.wav")
    write_dawproject(root, path)


def main(argv):
    out = argv[1] if len(argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "cubase")
    os.makedirs(out, exist_ok=True)

    make_demo_session(os.path.join(out, "demo_session.dawproject"))

    make_dualfilter(os.path.join(out, "dualfilter_a.dawproject"), -0.5)
    make_dualfilter(os.path.join(out, "dualfilter_b.dawproject"), +0.5)
    render_dualfilter(os.path.join(out, "dualfilter_a.wav"), -0.5)
    render_dualfilter(os.path.join(out, "dualfilter_b.wav"), +0.5)

    make_routing(os.path.join(out, "routing_a.dawproject"), with_send=False)
    make_routing(os.path.join(out, "routing_b.dawproject"), with_send=True)
    render_routing(os.path.join(out, "routing_a.wav"), with_reverb=False)
    render_routing(os.path.join(out, "routing_b.wav"), with_reverb=True)

    make_folder_group(os.path.join(out, "folder_group.dawproject"))

    make_vca_sends(os.path.join(out, "vca_sends.dawproject"))

    make_opaque(os.path.join(out, "opaque_a.dawproject"), -0.5)
    make_opaque(os.path.join(out, "opaque_b.dawproject"), +0.5)

    # VST3 preset pair: same plug-in (class id), one controlled state change.
    write_vstpreset(os.path.join(out, "dualfilter_a.vstpreset"),
                    DUALFILTER_CLASS_ID, "DualFilter", "Dark",
                    b"DFSTATE\x00Position=-0.500\x00Resonance=0.300")
    write_vstpreset(os.path.join(out, "dualfilter_b.vstpreset"),
                    DUALFILTER_CLASS_ID, "DualFilter", "Bright",
                    b"DFSTATE\x00Position=+0.500\x00Resonance=0.300")

    # Performed MIDI + its notated interpretation (Eb spelling of key 63).
    write_midi(os.path.join(out, "score_perf.mid"),
               [(0, 1, 60, 100), (1, 1, 63, 96), (2, 1, 65, 92),
                (3, 1, 67, 88), (4, 1, 72, 84)], name="Lead")
    write_musicxml(os.path.join(out, "score.musicxml"))

    write_midi(os.path.join(out, "notes.mid"),
               [(0, 1, 60, 100), (1, 1, 64, 96), (2, 1, 67, 92), (3, 1, 72, 88)])

    import json
    manifest = {
        "generator": "make_fixtures.py",
        "format": "dawproject (open MIT format imported/exported by Cubase 14/15)",
        "fixtures": {
            "demo_session.dawproject": {
                "expected": {"tracks_min": 5, "has_group": True, "has_fx_channel": True,
                             "has_instrument": True, "has_automation": True, "tempo": 120.0}},
            "dualfilter_a.dawproject": {"expected": {
                "field": "track[Gtr Wide].insert[DualFilter].param[Position]", "value": 0.25}},
            "dualfilter_b.dawproject": {"expected": {
                "field": "track[Gtr Wide].insert[DualFilter].param[Position]", "value": 0.75}},
            "routing_a.dawproject": {"expected": {"sends": 0}},
            "routing_b.dawproject": {"expected": {"sends": 1, "new_node": "FX 1 - Plate"}},
            "folder_group.dawproject": {"expected": {
                "folders": 1, "folder_group_channel_enabled": True,
                "children": ["Kick", "Snare"]}},
            "vca_sends.dawproject": {"expected": {
                "vca": "Drum VCA", "controls": ["Kick", "Snare"],
                "vca_sums_audio": False,
                "send_destination_widths": {"ch-fx1": 2, "ch-fx2": 1}}},
            "opaque_a.dawproject": {"expected": {
                "field": "DualFilter opaque <State> blob (Position=-0.500)",
                "note": "value not readable; blob hash differs from opaque_b"}},
            "opaque_b.dawproject": {"expected": {
                "field": "DualFilter opaque <State> blob (Position=+0.500)"}},
        },
        "renders": ["dualfilter_a.wav", "dualfilter_b.wav", "routing_a.wav", "routing_b.wav"],
    }
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Fixtures written to {out}")
    for name in sorted(os.listdir(out)):
        print("  ", name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
