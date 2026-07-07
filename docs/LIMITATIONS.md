# Limitations

Stated plainly, because honesty about the observability boundary is the research
contribution — not a disclaimer to bury.

## What v0 does not do

1. **No structural `.cpr` decoding.** The binary is scanned for evidence only
   (`cpr-lab`): container tokens, version, string/plug-in-name candidates with
   offsets and sub-1.0 confidence. We do not reconstruct tracks/routing/params
   from it. See `CPR_REVERSE_ENGINEERING_NOTEBOOK.md`.
2. **No fabricated plug-in parameter values.** DAWproject stores plug-in state
   as an opaque blob; the CPR is binary; the MIDI Remote API reaches only 8
   Quick Controls. Parameter *values* are recorded as `unknown_state`, with the
   surfaces that could lift them, never as invented numbers.
3. **MIDI Remote is not a project API.** It observes the selected channel +
   transport + a mixer bank + Quick Controls. Insert enumeration, routing,
   folders, and automation curves are not exposed (`MIDI_REMOTE_CAPABILITY_REPORT.md`).
4. **Score/notation is parsed from MusicXML only, modestly.** `ScoreState` now
   carries parts, key/time signatures and pitched notes *with spelling* from
   `.musicxml`/`.mxl`, and the performed-vs-notated comparison flags enharmonic
   reinterpretations. Not covered: Dorico interchange, layout/engraving detail,
   `score-timewise`, unpitched notes, timing-aligned matching (the comparison is
   a pitch multiset, stated in its output).
5. **Chord/scale, tempo maps, CC/pitch-bend/aftertouch** are modelled but only
   partially populated from current surfaces.
6. **Heuristics are labelled, never asserted as fact.** `role` and
   `device_family` come from keyword classifiers and are tagged `inferred` with
   confidence; they are backfill, not DAW truth.
7. **Descriptors are baseline, not perceptual.** RMS, crest, spectral centroid/
   rolloff/bandwidth, ZCR, LUFS — interpretable summaries, not mastering-grade or
   perceptual ground truth.
8. **Synthetic fixtures validate the pipeline, not the real app.** They are valid
   DAWproject files but authored by us; grounding against Cubase-exported
   artifacts is the documented next tier (`CUBASE_FIXTURE_PROTOCOL.md`).
9. **DAWproject variability.** Exporters vary (element vs. attribute placement,
   device element names). The parser is tolerant and keeps unknowns in
   `raw_source`. It is validated against our fixtures, the published XSD, **and a
   real Bitwig-exported example** (`tests/test_real_dawproject.py`) — but not yet
   against a genuine *Cubase*-emitted file (the documented next step needs a
   licensed Cubase; see `CUBASE_EXPORT_INSTRUCTIONS.md`).

10. **Cubase's exporter is narrower than the format.** Independent testing +
    Steinberg staff confirm Cubase 15's DAWproject export commonly **omits
    automation**, and does not export **MIDI CC/CC64/Note Expression/channel
    strip/crossfades**; **plug-in state is best-effort** (engine recreated,
    settings often lost). So a real Cubase export may legitimately yield no
    automation lanes and opaque inserts — reported honestly, not a parser bug.
    Enumerable built-in-device `<Parameters>` (when Cubase writes them) *are*
    read as observed values. See `CUBASE_CAPABILITY_MATRIX.md`.

## Known sharp edges

- Group-vs-folder classification uses routing topology + presence of a channel;
  an unusual project could be mis-bucketed (flagged in `field_provenance`).
- Diff matches entities by stable id then name; a rename with no id continuity
  reads as remove+add (mitigated by source-stable ids where available).
- The `demo` renders are synthesized signals chosen to *have* a measurable delta;
  they demonstrate the pipeline, not a specific plug-in's true acoustic behaviour.

## What we explicitly did not do (anti-goals honoured)

No 70-page design doc without code; no pretending the MIDI Remote API exposes
everything; no fragile regex "parser" sold as reverse engineering; no claim that
nearby strings prove structure; no overwriting of project files; no fake AI
assistant; no claim the PhD problem is solved.
