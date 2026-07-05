# Cubase State Ontology

Reading the Cubase documentation as evidence for an *ontology of Cubase state*
(not a user manual). Each concept below is annotated with its home in the
schema and its observability. This is the conceptual backbone the capability
matrix and observation model operationalize.

## Project state
project name · path · version · sample rate · frame rate · record format ·
project start/length · cursor · left/right locator · tempo mode (fixed/track) ·
fixed tempo · tempo map · time signatures.
→ `ProjectMeta`, `MusicalStructure`. Mostly `dawproject`-observable; tempo map
partial.

## Track state
audio · MIDI · **instrument** · group channel · effect (FX) channel · folder ·
ruler · chord · video · marker · automation tracks. Plus order, hierarchy,
visibility, mute, solo, record-enable, monitor, **freeze**, color, name,
channel configuration.
→ `TrackState` (12 `track_type`s), `FolderState`. `native.cubase` holds freeze,
monitor, channel role.

## Folder & group state (a distinction we refuse to flatten)
A Cubase folder may be **organizational only**, or have an **enabled group
channel** that sums its children:
```
folder: {organizational_only: true}
folder: {organizational_only: false, group_channel_enabled: true, summed_children:[…]}
```
→ `FolderState.organizational_only` / `group_channel_enabled`; graph uses
`CONTAINS` vs `SUMS`.

## Routing state
input/output buses · track input/output routing · group routing · FX-channel
routing · sends (destination, amount, enabled, pre/post when recoverable) ·
sidechains (when recoverable) · instrument MIDI routing.
→ `RouteState`, `SendState`. Output routing + sends are `dawproject`-direct;
input routing + sidechain generally unavailable → `unknown_state`.

## Insert state
per slot: index · plug-in name · vendor · identifier · **format (VST3/…)** ·
bypass · active · preset · parameters · state-blob ref · latency · confidence ·
provenance.
→ `DeviceState`. Identity/order/preset/format observable; **parameter values
opaque** (state blob) → `unknown_state`.

## Channel state
volume · pan · mute · solo · channel configuration · routing · EQ · channel
strip · inserts · sends · automation status.
→ folded into `TrackState` + `DeviceState(device_type="channel_strip")`.

## Automation state
automated parameter identity · track/plug-in association · event points · curve
semantics (ramp vs step) · read/write · muted · static-vs-automated value.
→ `AutomationLane`/`AutomationPoint` (**PARAMETER → LANE → EVENTS → CURVE**).
`dawproject`-observable.

## VST instrument state
rack instrument vs instrument track · identity · instrument channel · MIDI
source track · audio output channel · preset · parameters · automation · routing.
→ instrument `DeviceState` on an `instrument` track; `native.cubase.is_instrument_track`.

## Preset state
track presets · instrument-track presets · VST presets · FX-chain presets — alt
state surfaces carrying insert identity/order/preset and sometimes mixer state.
→ `preset` source type; `preset_lab` is a roadmap tool.

## MIDI state
parts · notes (pitch/onset/duration/velocity/channel) · controllers · program
changes · pitch bend · aftertouch · RPN/NRPN · MIDI automation · drum maps.
→ `ClipState.notes`/`MidiNote` (notes v0; CC/PB/AT roadmap).

## Musical structure state
chord track + chord/scale events · tempo · time signatures · markers · cycle
markers. → `MusicalStructure` (`ChordEvent`, `Marker(cycle)`, `TempoEvent`).

## Score state (representational layer)
instrument type · voices · layouts · note spelling · display quantization · key
sigs · clefs · dynamics · playing techniques · lyrics · concert vs transposed.
→ `ScoreState` (schema-ready; parse deferred). **Key idea:** the same MIDI
performance yields many notations, so *raw event state ≠ interpreted symbolic
state*. This separates acoustically-active from representational state.

## State taxonomy (a primary research output)
- **A. Acoustically active** — inserts, parameters, gain, pan, routing, sends,
  automation. Likely changes the render.
- **B. Structurally active** — routing/folder/group topology. May change render.
- **C. Musical content** — notes, timing, harmony, tempo.
- **D. Representational** — notation/score display. Usually inert acoustically.
- **E. Workflow** — selection, view, layout, editor state.
- **F. Unknown** — beyond current observability (`unknown_state`).

The schema tags each entity so a change can be classified into this taxonomy —
the bridge between "what changed in the state" and "did the sound change".
