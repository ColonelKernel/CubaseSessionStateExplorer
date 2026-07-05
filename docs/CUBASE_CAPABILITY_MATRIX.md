# Cubase Capability Matrix

Which extraction surface yields which state field, and at what access level.
This grid drives engineering priorities and is the source of truth behind the
declarative `observation_model.py` (which the fusion layer uses to compute
coverage and unknown-state).

Access levels: **D** direct/high-confidence · **I** indirect/inference ·
**P** partial · **E** experimental · **U** unavailable · **?** unknown.

| State field | CPR | MIDI Remote | Preset | DAWproject | MIDI | MusicXML | Dorico | Audio | Confidence | v0 |
|---|---|---|---|---|---|---|---|---|---|---|
| project name | I | P | U | **D** | I | U | ? | U | high | ✅ |
| sample rate | I | U | U | I | U | U | ? | **D** | med | ◐ |
| project length | I | U | U | I | U | U | ? | D | low | ○ |
| tempo (fixed) | I | **D** | U | **D** | **D** | U | ? | I | high | ✅ |
| tempo map | I | U | U | P | I | U | ? | U | low | ○ |
| time signatures | I | U | U | **D** | **D** | **D** | ? | U | high | ✅ |
| markers | I | U | U | P | U | U | ? | U | low | ✅ (schema) |
| tracks (count) | I | P | U | **D** | I | I | ? | I | high | ✅ |
| track types | I | P | U | **D** | I | U | ? | U | high | ✅ |
| track names | **I** | P | I | **D** | **D** | **D** | ? | I | high | ✅ |
| track colors | I | U | U | **D** | U | U | ? | U | high | ✅ |
| hierarchy (folders) | I | U | U | **D** | U | U | ? | U | med | ✅ |
| folder group-channel | I | U | U | I | U | U | ? | U | med | ✅ (native) |
| mute / solo | U | **D** | P | **D** | U | U | ? | U | high | ✅ |
| record enable | U | P | U | U | U | U | ? | U | low | ○ |
| volume | U | **D** | P | **D** | U | U | ? | I | high | ✅ |
| pan | U | **D** | P | **D** | U | U | ? | I | high | ✅ |
| input routing | U | P | U | U | U | U | ? | U | low | ○ |
| output routing | U | U | U | **D** | U | U | ? | U | high | ✅ |
| sends | U | U | I | **D** | U | U | ? | U | high | ✅ |
| send levels | U | U | I | **D** | U | U | ? | U | high | ✅ |
| insert names | **I** | U | **D** | **D** | U | U | ? | U | high | ✅ |
| insert order | I | U | **D** | **D** | U | U | ? | U | high | ✅ |
| bypass state | U | U | P | **D** | U | U | ? | U | med | ✅ |
| **plug-in parameters** | U | **P** (QC) | **P** | **U** (opaque blob) | U | U | ? | U | **low** | ✅ as *unavailable* |
| plug-in presets | I | U | **D** | I | U | U | ? | U | med | ✅ |
| automation lanes | U | U | U | **D** | I | U | ? | U | high | ✅ |
| automation events | U | U | U | **D** | I | U | ? | U | high | ✅ |
| audio events | I | U | U | **D** | U | U | ? | D | med | ✅ |
| audio file refs | I | U | U | **D** | U | U | ? | D | high | ✅ |
| MIDI parts | I | U | U | **D** | **D** | I | ? | U | high | ✅ |
| MIDI notes | U | U | U | **D** | **D** | I | ? | U | high | ✅ |
| VST instruments | I | P | **D** | **D** | U | U | ? | U | high | ✅ |
| chord events | I | U | U | P | U | U | ? | U | low | ✅ (schema) |
| notation state | U | U | U | U | U | **D** | **D** | U | med | ✅ (schema) |
| rendered audio | U | U | U | U | U | U | ? | **D** | high | ✅ |

Legend for the last column: ✅ implemented in v0 · ◐ available when the artifact
is present · ○ schema-ready, extraction deferred.

## Reading the matrix

- **DAWproject is the backbone.** It is the only surface that is `D` for
  routing, sends, automation, insert order, and hierarchy simultaneously. This
  is why the fusion layer chooses it as the structural base.
- **Plug-in parameter *values* are the hard wall.** No files-only surface
  exposes them: DAWproject stores an opaque state blob, CPR is binary, and the
  MIDI Remote API only reaches the 8 generic Quick Controls. v0 therefore
  records them as `unknown_state` with the exact surfaces that *could* lift them
  (preset export, Quick Controls, a custom VST3 `StateProbe`). This honesty is
  the point, not a gap to paper over.
- **Runtime (MIDI Remote) is `D` where files are `U`** — mute/solo/volume/pan —
  because it observes the live mixer. The fusion layer uses it to upgrade those
  fields to `observed` ground truth (and flags conflicts).
- **Notation is a separate axis** (MusicXML/Dorico), acoustically inert but
  representationally rich — schema-ready for the score-state research line.
