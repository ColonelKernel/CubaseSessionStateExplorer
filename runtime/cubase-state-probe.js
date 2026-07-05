/*
 * cubase-state-probe.js — a MIDI Remote API capability probe + state exporter.
 *
 * Cubase MIDI Remote API (ES5 JavaScript). Docs:
 *   https://steinbergmedia.github.io/midiremote_api_doc/
 *
 * PURPOSE
 *   This is NOT a full project exporter — the MIDI Remote API is a control-
 *   surface API. It exposes the *selected* mixer channel (name, volume, pan,
 *   mute, solo) and transport (tempo, playing), plus a mixer bank. This probe:
 *     1. binds those host values,
 *     2. empirically records which bindings actually deliver values
 *        (the capability probe — do not infer capability from API naming), and
 *     3. mirrors the observed values out as MIDI CC/SysEx on a virtual output
 *        so the companion bridge_listener.py can assemble a runtime snapshot
 *        JSON that fuses into the Session State Explorer.
 *
 * HONEST BOUNDARY (reflected in docs/MIDI_REMOTE_CAPABILITY_REPORT.md):
 *   Observable:   selected channel name/volume/pan/mute/solo, transport, and a
 *                 bank of channels; Quick Controls (8 generic plug-in params).
 *   NOT observable: insert slot enumeration, arbitrary VST3 parameters,
 *                 routing/sends topology, folder structure, automation curves.
 *
 * INSTALL
 *   Place under the Cubase MIDI Remote "Driver Scripts/Local" user folder and
 *   pair with a virtual MIDI port (e.g. the IAC Driver on macOS). The API does
 *   NOT provide filesystem or network access, so the bridge is MIDI-only.
 */

var midiremote_api = require('midiremote_api_v1')

var deviceDriver = midiremote_api.makeDeviceDriver(
    'ResearchProbe', 'StateProbe', 'Session State Explorer'
)

// A virtual (script-created) port pair; the user maps it to an IAC/loopMIDI port.
var midiInput = deviceDriver.mPorts.makeMidiInput('Probe In')
var midiOutput = deviceDriver.mPorts.makeMidiOutput('Probe Out')

deviceDriver.makeDetectionUnit()
    .detectPortPair(midiInput, midiOutput)
    .expectInputNameContains('Probe')
    .expectOutputNameContains('Probe')

var surface = deviceDriver.mSurface

// ---- Capability probe bookkeeping ---------------------------------------
// We record, at runtime, which host values ever fire onTitleChange /
// onProcessValueChange. That empirical record is the capability matrix.
var CAPS = {
    selected_channel_name: false,
    selected_channel_volume: false,
    selected_channel_pan: false,
    selected_channel_mute: false,
    selected_channel_solo: false,
    transport_tempo: false,
    mixer_bank_names: false,
    quick_controls: false,
}

// ---- Bind the selected channel ------------------------------------------
var selectedChannel = deviceDriver.mMapping.makePage('Probe').mHostAccess
    .mTrackSelection.mMixerChannel

var faderCC = 7, panCC = 10, muteCC = 20, soloCC = 21, tempoCC = 22

function bindValue(hostValue, ccNumber, capKey) {
    // A hidden surface control we can bind a host value to, so we receive
    // onProcessValueChange callbacks carrying the current value.
    var knob = surface.makeCustomValueVariable(capKey)
    knob.mOnProcessValueChange = function (activeDevice, value) {
        CAPS[capKey] = true
        // Mirror out as CC (0..127) on channel 1 so the bridge can read it.
        midiOutput.sendMidi(activeDevice, [0xB0, ccNumber, Math.round(value * 127)])
    }
    hostValue.mOnProcessValueChange = knob.mOnProcessValueChange
    return knob
}

bindValue(selectedChannel.mValue.mVolume, faderCC, 'selected_channel_volume')
bindValue(selectedChannel.mValue.mPan, panCC, 'selected_channel_pan')
bindValue(selectedChannel.mValue.mMute, muteCC, 'selected_channel_mute')
bindValue(selectedChannel.mValue.mSolo, soloCC, 'selected_channel_solo')

// Track name via title change.
selectedChannel.mOnTitleChange = function (activeDevice, objectTitle, valueTitle) {
    CAPS.selected_channel_name = true
    // Names cannot fit in a CC; emit as SysEx text the bridge parses.
    var bytes = [0xF0, 0x7D] // 0x7D = non-commercial/research manufacturer id
    for (var i = 0; i < objectTitle.length; i++) bytes.push(objectTitle.charCodeAt(i) & 0x7F)
    bytes.push(0xF7)
    midiOutput.sendMidi(activeDevice, bytes)
}

// Transport tempo.
var transport = deviceDriver.mMapping.makePage('Probe').mHostAccess.mTransport
var tempoKnob = surface.makeCustomValueVariable('tempo')
tempoKnob.mOnProcessValueChange = function (activeDevice, value) {
    CAPS.transport_tempo = true
    midiOutput.sendMidi(activeDevice, [0xB0, tempoCC, Math.round((value * 300) & 0x7F)])
}
transport.mValue.mTempo.mOnProcessValueChange = tempoKnob.mOnProcessValueChange

// Quick Controls (8 generic, host-exposed plug-in parameters for the
// selected channel) — the ONLY plug-in-parameter surface the API offers.
var qc = selectedChannel.mQuickControls
for (var q = 0; q < 8; q++) {
    (function (idx) {
        var qKnob = surface.makeCustomValueVariable('qc' + idx)
        qKnob.mOnProcessValueChange = function (activeDevice, value) {
            CAPS.quick_controls = true
            midiOutput.sendMidi(activeDevice, [0xB0, 40 + idx, Math.round(value * 127)])
        }
        qc.getByIndex(idx).mValue.mOnProcessValueChange = qKnob.mOnProcessValueChange
    })(q)
}

/*
 * The bridge_listener.py process listens on the paired MIDI input, decodes
 * these CC/SysEx messages into a channels[] + transport{} + capability{}
 * structure, and writes runtime/snapshot.json — which the extractor
 * cubase_session_explorer.extractors.runtime then ingests.
 */
