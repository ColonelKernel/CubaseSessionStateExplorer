# Validation

Validation is fixture-driven: for every extracted property we compare an
**EXPECTED** value (from the fixture's known construction) with the **OBSERVED**
extraction, and assert **MATCH**. For unavailable data we assert the system
returns explicit `unknown_state` rather than an invented value.

## Test suite (`tests/`, 19 passing)

Run: `python -m pytest -q` (fixtures auto-generate on first run via
`conftest.py`).

| Test | Expected | Observed check |
|---|---|---|
| `test_demo_session_structure` | tempo 120, 4/4, ≥4 tracks, a group, 1 FX, master, v15.0.10 | structural parse |
| `test_demo_session_devices_and_families` | StudioEQ/DeEsser/Retrologue/REVerence present; EQ family **inferred** | device parse + heuristic provenance |
| `test_instrument_track_has_notes` | Synth Pad = 4 notes, first key 60 | MIDI-in-DAWproject |
| `test_routing_and_sends` | 1 send → FX; ≥2 tracks route into group | routing resolution |
| `test_automation_lane` | 1 lane, 3 points | automation parse |
| `test_plugin_parameters_are_explicitly_unavailable` | 0 fabricated params; `plugin_parameters` + `insert_parameter_state` gaps present | honesty guarantee |
| `test_coverage_is_reported_and_bounded` | 0 < coverage < 100 | partial observability |
| `test_provenance_is_attached` | vox = exported/dawproject | provenance |
| `test_dualfilter_parameter_change_detected` | PARAMETER change `[0.25]→[0.75]` | diff classification |
| `test_routing_change_detected` | ROUTING + STRUCTURAL; sends 0→1 | diff classification |
| `test_audio_delta_measurable` | some descriptor Δ > 1e-4 | state→audio link |
| `test_identical_sessions_no_changes` | empty diff | diff stability |
| `test_midi_extractor` | 120 bpm, 4 notes, key 60 | SMF parser |
| `test_cpr_lab_*` | tokens + DualFilter candidate; graceful on missing | CPR evidence |
| `test_bundle_ingest_missing_path_is_graceful` | evidence-only session, no crash | robustness |
| `test_snapshot_roundtrip` | reloaded == saved | persistence |
| `test_graph_builds_without_networkx` | >10 nodes, `SENDS_TO` edge exists | graph |
| `test_track_archive_and_dawproject_bundle` | runtime overlays mute→observed(midi_remote) | fusion |

## Validated properties (per the required list)

- **track count / names / hierarchy / insert identity / insert order / routing /
  MIDI notes / tempo / automation points** — validated where the DAWproject
  surface permits (see the table).
- **Unavailable data** — validated that plug-in parameter values yield explicit
  `unknown_state` records, never invented numbers.

## Build-for-failure checks

Extractors never raise on bad input (missing file, bad zip, non-XML, truncated
MIDI): they return partial results + warnings. `test_cpr_lab_on_nonexistent_is_graceful`
and `test_bundle_ingest_missing_path_is_graceful` assert this. Unknown DAWproject
elements are retained in `raw_source`/`native`, not dropped.

## Coverage metric (explainable, not an "AI score")

`observation_model.coverage()` counts revealed canonical fields fully and
constrained fields at half weight over the fixed 34-field set. The demo session
reports **72.1%** with an itemized revealed/constrained/hidden breakdown, printed
in the extraction report. It is fully reproducible and inspectable.

## Grounding against real Cubase (next tier)

The synthetic tier validates the *pipeline*. The Cubase-authored fixture tier
(`CUBASE_FIXTURE_PROTOCOL.md`) validates the *real surfaces*: export each
protocol project from Cubase 15 as `.dawproject` + Track Archive + `.cpr` +
render, and re-run the same expected-vs-observed assertions. This is the
verification step that requires a licensed Cubase and is left as a documented,
repeatable procedure.
