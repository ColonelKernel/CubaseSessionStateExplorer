# Cubase Adapter Mapping

How canonical (cross-DAW) concepts map onto each prior prototype and onto
Cubase. The rule: **preserve distinctions, do not force fake equivalence.**
Where Cubase carries a semantic the canonical layer would flatten, it lives in
`native.cubase` on the entity (Layer 2), never lost.

## Concept mapping

| Canonical concept | REAPER | Ableton | Logic | Cubase (this adapter) |
|---|---|---|---|---|
| Project | `.rpp` `ProjectState` | Live Set | `SessionEvidence` (from exports) | `.cpr` / `.dawproject` → `SessionState` + `ProjectMeta` |
| Track (audio) | `TrackState` | Audio Track | `InferredTrackState` | `TrackState(track_type="audio")` |
| Track (MIDI/instrument) | MIDI track | MIDI Track | (from MIDI export) | `track_type="midi"` / `"instrument"` (+ `native.cubase.is_instrument_track`) — **not** "just a MIDI track" |
| Clip / Item / Region | `MediaItemState` | `ClipState` (session/arr) | audio stem evidence | `ClipState` (Audio Event / MIDI Part), beats **and** seconds |
| Device / FX / plug-in | `FxState` | `DeviceState` | user-asserted | `DeviceState` (+ `plugin_format` VST3/VST2/AU/internal, `state_blob_ref`) |
| Device parameter | (not decoded) | `DeviceParameterState` | — | `DeviceParameterState`; VST3 `is_visible_to_host`/`is_automated`; **values opaque → unknown_state** |
| Bus / Return / Aux | send target track | `ReturnTrackState` | hidden | FX Channel → `return_tracks` (`track_type="fx"`) |
| Group | folder/submix (warned) | Group Track | hidden | `groups` (`track_type="group"`) — a **summing** bus |
| Folder | `FOLDER` (flattened) | group | track stack (hidden) | `FolderState`: **organizational_only vs. group_channel_enabled** kept distinct |
| Routing edge (output) | `AUXRECV`/main send | `routes_to_master` | hidden | `RouteState(route_type="output")` → graph `ROUTES_TO`/`SUMS` |
| Send | `RouteState` (send_mode) | `SendState` | hidden | `SendState` (`pre_fader` when recoverable) → graph `SENDS_TO` (**not** containment) |
| Automation | not modelled | `is_automated` flag only | hidden | `AutomationLane` → `AutomationPoint[]` w/ curve type (**first-class**) |
| Instrument | — | device (instrument) | — | instrument `DeviceState` on `instrument` track + MIDI source |
| Tempo state | scalar | scalar | from MIDI | `tempo` + `MusicalStructure.tempo_map` (ramp/jump) |
| Time signature | num/denom | string | from MIDI/MusicXML | `TimeSignatureEvent[]` |
| Musical notation | — | — | MusicXML (notation only) | `ScoreState` (schema present; parse deferred) — **distinct from performed MIDI** |
| Markers / cycle | — | — | — | `Marker` (`cycle` flag) |
| Chord track | — | — | — | `ChordEvent[]` (schema present) |
| Master | implicit | `MasterTrackState` | mixdown | `master_track` (`track_type="master"`, Stereo Out) |
| Unparsed remainder | `raw_lines` | `raw_source` | `raw_source` | `raw_source` + `native.cubase` + `unknown_state` |

## Distinctions we deliberately preserve

- **A folder is not always a group.** Cubase folder tracks may be purely
  organizational *or* have an enabled group channel that sums children.
  `FolderState.organizational_only` vs `group_channel_enabled` + a `SUMS` (not
  `CONTAINS`) graph edge encode the difference. Flattening these would erase a
  real signal-flow fact.
- **An instrument track is not a MIDI track.** It couples a MIDI source, a VST
  instrument, and an audio output channel. `native.cubase.is_instrument_track`
  and the instrument `DeviceState` keep this explicit.
- **A send is not parent-child containment.** `SENDS_TO` is a distinct edge type
  from `CONTAINS`/`SUMS`; a send has level/pan/pre-post, not membership.
- **Performed vs. notated state.** `ClipState.notes` (MIDI performance) and
  `ScoreState` (notation) are separate layers: the same performance yields many
  notations. This underpins the acoustically-active / representational-state
  taxonomy (see `RESEARCH_ALIGNMENT.md`).
- **Scenes gap.** Cubase's linear Arranger has no session grid; we simply omit
  scenes and use `start_time_beats` + markers, rather than inventing a false
  equivalent.

## The adapter contract

The prior repos have no single formal `DawAdapter` interface; the shared package
intends one. This adapter realizes it as concrete modules so it can plug in later
without a rewrite:

| Contract method | Realized by |
|---|---|
| `detect(input)` | `bundle.classify` / `cpr_lab.scan.is_probable_cpr` |
| `discover(input)` | `bundle.discover` → `Bundle` |
| `extract(input)` | `extractors/*.extract` → partial `SessionState` |
| `normalize(evidence)` | `fusion.fuse` → canonical `SessionState` |
| `buildGraph(state)` | `graph_builder.build_graph_dict` / `build_networkx` |
| `validate(state)` | `tests/` + `report.extraction_report` + `VALIDATION.md` |
