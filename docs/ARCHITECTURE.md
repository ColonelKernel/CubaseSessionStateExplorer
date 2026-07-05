# Architecture

## Layers

```
              ┌────────────────────────────────────────────────┐
 INPUT        │  a .dawproject / .cpr / .xml / .mid / render /  │
              │  runtime.json  — or a bundle directory of these │
              └───────────────────────┬────────────────────────┘
                                       │
 DISCOVERY   bundle.discover ──────────┤  classify each file → Artifact(type)
                                       │
 EXTRACTION  extractors/ ──────────────┤  each artifact → PARTIAL SessionState,
   dawproject · track_archive ·        │  every value carrying Provenance
   cpr_lab · midi · runtime            │
                                       │
 FUSION      fusion.fuse ──────────────┤  1 structural base (DAWproject>TrackArchive)
                                       │  2 runtime overlay (observed ground truth)
                                       │  3 CPR/MIDI corroboration
                                       │  4 observation_model → coverage + unknown_state
                                       │  5 heuristic backfill (role/family, inferred)
                                       │
 CANONICAL   models.SessionState ──────┤  cross-DAW schema + native.cubase (lossless)
                                       │
 DERIVED     graph_builder ── snapshot ── diff ── audio_descriptors ── intervention
                                       │
 SURFACES    cli.py  ·  app.py (Streamlit)  ·  cpr-lab CLI  ·  runtime bridge
```

## Modules

| Module | Responsibility |
|---|---|
| `provenance.py` | `Provenance` (status, confidence, `EvidenceSource`), constructors, conflict/unavailable. |
| `models.py` | Canonical `SessionState` and children; `Provenanced` mixin; `native.cubase`; `UnknownState`; heuristic backfill. |
| `ids.py` | Dialect-namespaced, **source-stable** ids so diffs survive re-ingest. |
| `observation_model.py` | Declarative artifact→field observability table; coverage + hidden-field derivation. |
| `bundle.py` | Artifact discovery, content sniffing, hashing. |
| `extractors/*` | One artifact type → partial state; never raise, always warn. |
| `fusion.py` | Merge evidence → one `SessionState`; derive `unknown_state` + coverage. |
| `graph_builder.py` | Typed graph (dict + optional networkx), provenance-coloured. |
| `snapshot.py` / `diff.py` | Persist canonical state; classified entity diff. |
| `audio_descriptors.py` | Tiered baseline acoustic descriptors + delta. |
| `intervention.py` | `StateIntervention`, `Observation`, `InterventionExperiment`, dataset export. |
| `report.py` | Human extraction report + explainable coverage. |
| `cli.py` / `app.py` | Command line + Streamlit instrument. |

## Design principles

1. **Evidence, not assertion.** Extractors emit provenance-tagged evidence;
   fusion decides. Nothing is fabricated; opaque state becomes `unknown_state`.
2. **Fusion over single-source.** The strongest structured surface is the base;
   runtime observations override it where they are ground truth (with conflict
   flags); CPR/MIDI corroborate.
3. **Lossless dual layer.** Portable fields (Layer 1) + `native.cubase` (Layer 2).
   The cross-DAW schema never becomes a pile of Cubase fields, and no Cubase
   semantics are dropped.
4. **Graceful degradation.** Core needs only `pydantic`; networkx, pyvis,
   pandas, numpy, librosa, mido, soundfile are all optional with fallbacks.
5. **Stable ids.** Source-derived ids keep snapshot diffs meaningful across edits.
6. **The observability boundary is data.** `unknown_state` + coverage are outputs,
   not omissions.

## Data flow for the headline experiment

`cli.experiment A.dawproject B.dawproject --render-a a.wav --render-b b.wav`:

1. `fusion.ingest` each → `SessionState` (A, B), saved as snapshots.
2. `diff.diff_sessions(A, B)` → classified `Change[]`.
3. `_infer_intervention` picks the dominant category → `StateIntervention`.
4. `audio_descriptors.extract` each render → `descriptor_delta`.
5. Assemble `InterventionExperiment` (state_delta + audio_delta) + JSONL
   `Observation` rows compatible with the other three DAWs.
