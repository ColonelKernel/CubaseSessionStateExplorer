# Prior Prototypes Audit

Audit of the three sibling prototypes on disk, conducted before committing the
Cubase architecture, so we **reuse proven concepts and infrastructure** rather
than rewrite. Repositories inspected (all Python, pydantic v2, Streamlit UI,
networkx graph, librosa descriptors):

- `/Volumes/Mac-Storage/GitHub/SessionStateExplorerReaper` — REAPER
- `/Volumes/Mac-Storage/GitHub/AbletonSessionStateExplorer` — Ableton Live
- `/Volumes/Mac-Storage/GitHub/LogicSessionStateExplorer` — Logic Pro
- `/Volumes/Mac-Storage/GitHub/SessionStateExplorer` — nascent *shared* package
  (`session_explorer.core`: `ids`, `provenance`, `utils`) + a `docs/cubase`
  design-argument mapping. Its `__init__` states the design intent this Cubase
  adapter follows: *"one product over four DAW dialects … the canonical schema
  is unified but lossless: every driver keeps its full native model attached."*

## Per-prototype summary (dimensions A–N)

### REAPER (`SessionStateExplorerReaper`)

| Dim | Finding |
|---|---|
| A. Extraction | Line-oriented tolerant parser of plain-text `.rpp` (`rpp_parser.py`, stack-based, never raises). |
| B. Available | Track name/vol/pan/mute/solo/color, FX name/type/family/bypass/offline/chain, media items, sends/receives w/ mode, tempo, sample rate. |
| C. Missing | Plug-in parameter values, automation envelopes, folder hierarchy, take FX, item fades — recorded as `warnings`. |
| D. Data model | `models.py`: `ProjectState/TrackState/FxState/RouteState/MediaItemState/AudioDescriptorSet/Recommendation`. `raw_lines` for traceability. |
| E. Graph | networkx `DiGraph`; nodes project/track/media_item/audio_file/fx/bus; edges contains_*/processes_with/sends_to. |
| F. UI | Streamlit; sidebar upload, summary metrics, PyVis graph (Plotly fallback), tables, descriptors, recommendations, export, fingerprint compare. |
| G. Snapshot | `export.py` full JSON bundle (project+graph+descriptors+recs+fingerprint). |
| H. Diff | Structural **fingerprint** + cosine similarity (not a true entity diff). |
| I. Provenance | `warnings` + shared `Provenance`; observability matrix per artifact. |
| J. Confidence | Per-recommendation confidence + calibrated caveats. |
| K. Reusable | graph_builder, audio_descriptors, visualization, export, fingerprint, classifiers. |
| L. Weak | No folder hierarchy; name-based comparison only; REAPER-specific recommendation text; no CLI. |
| M. Native | rec-input FX chain, offline state, 4 send-modes, pan law, width, 6 solo-modes, FX containers. |
| N. Cross-DAW | Track/Clip/Processor/Route/return/master abstractions. |

### Ableton (`AbletonSessionStateExplorer`)

| Dim | Finding |
|---|---|
| A. Extraction | **Live extension** (TS, Extensions SDK) exports canonical JSON at runtime; plus a shallow `.als` gzip-XML *inspector* (explicitly not a parser). |
| B. Available | Tracks/clips/devices/parameters/sends/returns/master/scenes/tempo; honest `null` for colour, device on/off, automation, dB values. |
| C. Missing | Automation curves, rack internals, macros, sidechain — `null`/`raw_source`. |
| D. Data model | `models.py`: `ProjectState/TrackState/ClipState/DeviceState/DeviceParameterState/SendState/ReturnTrackState/MasterTrackState/SceneState`. **This is the schema shape the Cubase adapter matches.** |
| E. Graph | networkx; 11 node / 12 edge types incl. `has_device`, `has_parameter`, `sends_to`, `routes_to_master`, `group_contains`. |
| F. UI | Streamlit; demo / upload-JSON / inspector modes; PyVis; prediction tab. |
| G. Snapshot | `export.py` bundle; schema_version pinned. |
| H. Diff | **Real entity diff** (`session_diff.py`): tracks added/removed, per-track device/send/volume/clip changes, parameter changes. Matches by **name** (its stated weakness). |
| I/J. Provenance/Conf | `raw_source` + `metadata.daw_dialect` + per-recommendation confidence/caveat. |
| K. Reusable | models, graph_builder, session_diff, audio_descriptors, recommendations, prediction, export, visualization. |
| L. Weak | Name-based diff matching; keyword-only classification; scenes gap for linear DAWs. |
| M. Native | return tracks, scenes, device chains/racks, warp. |
| N. Cross-DAW | Already ships a `cubase_session_model.py` (hand-authored demo) + `docs/cubase_mapping.md` + `track_archive_inspector.py`. |

### Logic (`LogicSessionStateExplorer`)

| Dim | Finding |
|---|---|
| A. Extraction | **Exports only** (`.logicx` is opaque): stems, mixdown, MIDI, MusicXML, channel-strip notes, manifest. `.logicx` never parsed. |
| B. Available | Audio content + descriptors, MIDI/MusicXML metadata, inferred track name/role; user-asserted plug-ins/sends/bus. |
| C. Missing | Plug-in chain, automation, routing, track stacks, VCA — represented as explicit **hidden state**. |
| D. Data model | `SessionEvidence` + `AudioEvidence/InferredTrackState/HiddenStateMarker/ChannelStripNote/Recommendation`. |
| E. Graph | 15 node / 12 edge types incl. `hidden_state_marker`; observability colours; **PROV-O export**. |
| F. UI | Streamlit; `.streamlit/config.toml`; observability legend. |
| G/H. Snap/Diff | JSON bundle; **no cross-session diff** (roadmap item). |
| I. Provenance | **The research heart**: a declarative `observation_model.py` (artifact → reveals/constrains/asserts/hides) from which `HiddenStateMarker`s are *derived*. This is the pattern the Cubase adapter adopts and generalizes. |
| J. Confidence | Role-inference confidence **calibrated** against MedleyDB (published in `docs/evaluation.md`). |
| K. Reusable | observation_model *pattern*, models, graph_builder, matching, audio_descriptors, signal_comparisons. |
| L. Weak | pydantic/dataclass dual path (skip — mandate pydantic); global id counter; per-session-only schema version. |
| M. Native | track stacks, VCA, Logic automation modes. |
| N. Cross-DAW | Observability boundary as a comparable, first-class object. |

## Decisions for the Cubase adapter (what we reuse / improve)

**Reused concepts**
1. Canonical schema shape from Ableton `models.py` (`ProjectState`/`TrackState`/
   `DeviceState`/`SendState`/…) → our `models.SessionState`.
2. `Provenance` + observability from the shared core → widened in `provenance.py`.
3. Logic's **declarative observation model** → `observation_model.py`, deriving
   coverage + `unknown_state` (the strongest reusable research idea).
4. networkx graph + Streamlit UI + librosa descriptors patterns.
5. Ableton's **real entity diff** approach (improved with stable ids).

**Improvements over the priors (Cubase advantages)**
1. **A real importer** via open DAWproject — no prior parses its own DAW's format.
   (Ableton/Logic can't; REAPER parses text but not parameters.)
2. **Stable, source-derived ids** (`ids.stable_id`) so diffs survive re-ingest —
   fixing the name-matching weakness in REAPER/Ableton.
3. **First-class automation lanes** (PARAMETER→LANE→POINTS→CURVE), absent in all
   three priors.
4. **Folder vs. group-channel-enabled folder** kept distinct (`FolderState` +
   `native.cubase`) — a Cubase-specific distinction that must not be flattened.
5. **Per-value provenance** (`field_provenance`) + explicit **conflict** status
   when runtime disagrees with a static export.
6. **Multi-source fusion** (structural base + runtime overlay + CPR/MIDI) rather
   than one surface.

**Weak decisions explicitly not repeated**: name-only diff matching; keyword-only
classification presented as fact; hidden state hard-coded; pydantic fallback shim;
DAW-specific recommendation text baked into the engine.
