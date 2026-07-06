# Roadmap

Ordered by research value × feasibility. v0 (this repo) covers the DAWproject
importer, CPR evidence lab, MIDI/track-archive/runtime extractors, fusion,
graph, snapshot/diff, state→audio experiments, UI, and docs.

## Near term

1. **Cubase-authored fixture grounding.** Export protocol projects 00–16 from
   Cubase 15 as `.dawproject` + Track Archive + `.cpr` + render; re-run the
   expected-vs-observed suite against real artifacts; feed `cpr-lab diff` real
   controlled pairs (validates CPR-004).
2. ~~**Preset lab.**~~ **Done (v0):** `extractors/vstpreset.py` + `preset-lab`
   CLI parse `.vstpreset` files per the official SDK format (header, class id,
   chunk list, MediaBay Info attributes) and fingerprint the opaque Comp/Cont
   state; fusion enriches matching devices with `preset_name` + fingerprints.
   Remaining: Cubase *track* presets / FX-chain presets (separate container
   formats), and grounding against Cubase-exported presets.
3. **DAWproject exporter variability hardening.** Test against Bitwig/Studio One
   exports too; expand element-name coverage; property-based fuzzing.
4. **Richer MIDI** — CC / pitch-bend / aftertouch / program change into
   `ClipState`; drum-map recovery.

## Mid term

5. **Live runtime capture** — run the MIDI Remote probe + bridge against a real
   Cubase; publish the measured capability matrix; wire Quick Controls into
   `DeviceParameterState` (partial, honest parameter observation).
6. ~~**Score-state extraction.**~~ **Done (v0, MusicXML):**
   `extractors/musicxml.py` parses parts, key/time signatures and pitched notes
   *with spelling* into `ScoreState`; `compare_performance_to_score` reports
   enharmonic reinterpretations (e.g. MIDI 63 notated Eb4 vs default D#4) as
   acoustically-inert representational divergence. Remaining: Dorico
   interchange, timing-aligned matching, display-quantization comparison.
7. **Structural fingerprint + similarity + retrieval**, reusing the REAPER/
   Ableton approach on the Cubase graph.
8. **Cross-DAW dataset** — unify `observations.jsonl` across REAPER/Ableton/
   Logic/Cubase; first prediction baselines (state → coarse acoustic descriptors).

## Longer term / PhD trajectory

9. **VST3 `StateProbe`** — a small research VST3 that records its own parameter
   state and host timing context, emitting synchronization markers between state
   and audio. Documents precisely *what a VST3 plug-in can and cannot observe*.
10. **Interpretable prediction & attribution** — models that predict acoustic
    deltas from state deltas and attribute an audio change to the responsible
    state change, kept inspectable via provenance.
11. **Cross-DAW observability study** — formalize and compare the observability
    boundary across the four DAWs as a research result in itself.
12. **Formal `DawAdapter` interface** in the shared package; register the Cubase
    adapter against it once REAPER/Ableton/Logic are refactored onto it.

## Explicit non-goals

Full proprietary `.cpr` decoding; a universal DAW ontology built before real
Cubase extraction; a consumer product; any claim the research question is solved.
