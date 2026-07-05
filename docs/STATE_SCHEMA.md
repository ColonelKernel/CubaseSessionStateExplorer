# Canonical State Schema

`SCHEMA_VERSION = "0.1.0"`. Defined in `src/cubase_session_explorer/models.py`
(pydantic v2). Every entity extends `Provenanced` (carries `provenance`,
`field_provenance`, `native`, `raw_source`).

## Root: `SessionState`

| Field | Type | Notes |
|---|---|---|
| `schema_version` | str | pinned |
| `adapter` | dict | `{daw, adapter_version}` |
| `project` | `ProjectMeta` | name, path, version, sample rate, locators, tempo mode |
| `tempo`, `time_signature` | float, str | convenience (initial values) |
| `tracks` | `TrackState[]` | audio/midi/instrument |
| `folders` | `FolderState[]` | organizational vs group-channel-enabled |
| `return_tracks` | `TrackState[]` | FX channels (`track_type="fx"`) |
| `groups` | `TrackState[]` | group buses (`track_type="group"`) |
| `master_track` | `TrackState?` | Stereo Out |
| `routes` | `RouteState[]` | output/input/sidechain edges |
| `automation` | `AutomationLane[]` | parameter → points → curve |
| `musical_structure` | `MusicalStructure` | tempo map, time sigs, markers, chords |
| `score_state` | `ScoreState` | notation layer (schema-ready) |
| `media` | `MediaFile[]` | referenced audio/video/midi |
| `unknown_state` | `UnknownState[]` | the observability boundary |
| `capture` | `CaptureInfo` | artifacts, extractors, coverage, timestamp |
| `warnings`, `metadata` | list, dict | |

Accessors: `all_tracks()`, `all_devices()`, `all_sends()`, `track_by_id()`,
`device_by_id()`.

## Key entities

**`TrackState`** — `id, index, name, track_type, role?, color?, parent_id?,
volume_db?, pan?, mute?, solo?, record_enabled?, monitor?, frozen?,
channel_config?, output_target_id?, clips[], devices[], sends[]`.
`track_type ∈ {audio, midi, instrument, group, fx, folder, marker, chord,
ruler, video, automation, master}`.

**`FolderState`** — `id, name, index, child_track_ids[], organizational_only,
group_channel_enabled`. The two booleans encode the Cubase folder-vs-group
distinction; `native.cubase.has_channel` corroborates.

**`DeviceState`** — `id, track_id, index (slot), name, vendor?,
plugin_identifier?, plugin_format?, device_type?, device_family?, enabled?,
bypassed?, preset_name?, state_blob_ref?, latency_samples?, parameters[]`.
When `state_blob_ref` is set, `field_provenance["parameters"]` is `unavailable`.

**`DeviceParameterState`** — `id, device_id, name, value?, normalized_value?,
unit?, is_automated?, is_visible_to_host?` (VST3-native notions).

**`SendState`** — `id, source_track_id, target_return_id, send_name?, level_db?,
pan?, enabled?, pre_fader?` (`None` ⇒ unrecoverable from this surface).

**`RouteState`** — `id, source_track_id, target_id, route_type ∈
{output, input, sidechain, instrument_out}`.

**`AutomationLane`** — `id, track_id, parameter_name, device_id?, parameter_id?,
read_enabled?, write_enabled?, muted?, unit?, points[]`.
**`AutomationPoint`** — `time_beats, value, curve ∈ {linear, step, ramp,
spline, unknown}`. Automation is modelled as **PARAMETER → LANE → TIMED EVENTS →
CURVE**, never a flat number list.

**`ClipState`** — audio/midi; beats **and** seconds; `notes: MidiNote[]`;
`midi_note_count`; `audio_file`/`media_id`.
**`MidiNote`** — `time_beats, duration_beats, key, velocity, channel, release_velocity?`.

**`MusicalStructure`** — `tempo_map[], time_signatures[], markers[], chords[]`.
**`ScoreState`** — `present, layouts[], notes?` (deliberately minimal in v0 so
notation can be added without a migration).

**`UnknownState`** — `id, entity_id?, state_gap, reason, potential_sources[],
severity ∈ {info, notable, blocking}`. First-class record of the unobservable.

## Provenance (`provenance.py`)

`Provenance{status, confidence, source:EvidenceSource{type, artifact, locator,
evidence}, explanation, alternatives[]}`.
`status ∈ {observed, exported, parsed, inferred, reconstructed, user_supplied,
unavailable, conflicting}`. `source.type ∈ {cpr, dawproject, track_archive,
preset, midi, musicxml, dorico, runtime_api, midi_remote, rendered_audio,
filesystem, manual_annotation, fusion}`. `qualitative()` buckets confidence to
high/medium/low/none for display.

## Analysis-side

**`AudioDescriptorSet`** — level (rms, peak, crest), spectral (centroid,
rolloff, bandwidth), zcr, stereo-width proxy, LUFS, mfcc means; `available`,
`warnings`. **`StateIntervention` / `Observation` / `InterventionExperiment`** —
see `intervention.py`; `Observation` is the dataset row shared across DAWs.
