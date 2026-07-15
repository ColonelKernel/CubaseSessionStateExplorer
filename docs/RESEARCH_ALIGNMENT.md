# Research Alignment (UPF MTG / Steinberg)

The PhD problem: *representations of the states of a DAW — audio effects, mixing
and routing details — and how they influence the final acoustic outcome.* This
prototype is built to be **evidence of research fit**: it operationalizes that
problem for Cubase, the hardest of the four DAWs, and does so honestly.

## The ten ideas, made concrete

| Idea | Where it lives in the prototype |
|---|---|
| 1. A DAW session is a structured computational object | `SessionState` (typed, validated, serializable) |
| 2. The same acoustic outcome can arise from different states | schema separates performed (`ClipState.notes`) from notated (`ScoreState`), and structural-vs-parametric routes to a bus | 
| 3. Small state changes can produce large acoustic changes | the DualFilter demo: one automation-point change → measurable spectral delta |
| 4. Some state variables are semantically interpretable | `device_family`, `role`, routing edges, `AutomationLane.parameter_name` |
| 5. A graph captures relations a flat vector misses | `graph_builder` distinguishes `SENDS_TO`/`SUMS`/`CONTAINS`/`AUTOMATES` |
| 6. Temporal state needs more than a static graph | `AutomationLane` → `AutomationPoint[]` with curve types |
| 7. Cross-DAW normalization must not erase native concepts | Layer 1 canonical + Layer 2 `native.cubase`; folder-vs-group preserved |
| 8. Observability and uncertainty must be modelled | `Provenance` on every value + `unknown_state` + coverage |
| 9. State/audio pairs can become a prediction dataset | `Observation`/`InterventionExperiment` → `observations.jsonl` |
| 10. Controlled interventions beat passive collection | `StateIntervention` pairs a diff with a known cause |

## The research loop the prototype instantiates

```
DAW STATE (SessionState, provenance-tracked)
   │  StateIntervention (known, controlled)
   ▼
STATE DELTA (diff.diff_sessions → classified change)
   ▼
ACOUSTIC DELTA (audio_descriptors.descriptor_delta on paired renders)
   ▼
OBSERVATION row (dataset, cross-DAW compatible)
```

`python -m cubase_session_explorer demo` runs this loop twice (a parameter
change and a routing change), writing real `InterventionExperiment` records.

## Enabled research questions

- **Prediction** — can broad acoustic characteristics be predicted from
  structured state? **Scaffold implemented (v0):** `prediction.py` +
  `state-audio-eval` define the task, provide a mean baseline and a
  nearest-fingerprint regressor, and evaluate leave-one-out with a skill-vs-mean
  metric — framed honestly as a methodology scaffold on synthetic fixtures, not
  a validated model. It already yields a genuine finding: structural fingerprints
  predict coarse *between-session* character but are **blind to within-A/B
  plug-in-parameter changes** (the fingerprint is identical across the pair), so
  the paired intervention layer, not structure, carries that acoustic delta —
  concrete evidence for why parameter-level state must enter the representation.
- **Retrieval / similarity** — which sessions share a production strategy?
  **Implemented (v0):** `fingerprint.py` turns a session into a scale-invariant,
  interpretable structural fingerprint built from canonical concepts only, with
  cosine+Jaccard `similarity`, `feature_deltas` (which axis explains a
  difference), and cross-DAW `retrieve_similar`. Because fingerprints ride in
  `observations.jsonl`, a REAPER/Ableton/Logic session enters the same retrieval
  corpus as Cubase without needing its parser — a concrete instance of the
  shared representation the cross-DAW thesis argues for.
- **Attribution** — which state change most explains an audio change?
  (Controlled `StateIntervention` pairs isolate a single cause.)
- **Interpretable assistance** — can a model suggest a transformation while
  remaining inspectable? (Every value's provenance is queryable.)
- **Cross-DAW universality** — which parts of a session are universal vs.
  DAW-specific? (Layer 1 vs. Layer 2; and the **observability boundary itself**
  differs per DAW — REAPER text vs. Ableton SDK vs. Logic exports vs. Cubase's
  many surfaces — which is itself a research finding.)

## Why this is strong evidence of fit

A Steinberg engineer sees an honest map of what Cubase exposes and where (the
capability matrix), built on the first-party DAWproject surface and a
capability-probed runtime bridge — not hallucinated APIs. An MIR researcher sees
a provenance-tracked, graph-structured intermediate representation with a
state→audio dataset and a controlled-intervention design. A producer sees their
routing, inserts, sends, and automation rendered faithfully, with the honest
statement of what cannot be recovered. All three can immediately see **why the
problem is hard and why the representation is interesting** — which is the point.
