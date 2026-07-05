# Producing Real Cubase Exports (for grounding the importer)

This is the manual step only you can do: create a few small Cubase 15 projects
and export them, so the importer can be validated and repaired against genuine
Cubase output (not our self-authored fixtures). Keep them tiny — the goal is
*controlled, known state*, not real music.

## What to export from each project

For every test project, produce as many of these as you can (more surfaces =
more we can fuse and cross-check):

| Artifact | How (Cubase 15, confirmed against Steinberg docs) | Why we need it |
|---|---|---|
| **`.dawproject`** (primary) | **File ▸ Export ▸ DAWproject** → name + location → **Save** (added in Cubase 14.0.20) | The high-confidence structured surface the importer parses |
| **Track Archive `.xml`** | Select the tracks → **File ▸ Export ▸ Selected Tracks…** → choose *Copy* or *Reference* media → **Save** (Cubase **Pro** only) | Secondary structural surface; validates the Track Archive extractor |
| **`.cpr`** | Just **File ▸ Save** — the project file itself | Feeds `cpr-lab` (evidence + controlled binary diff) |
| **Mix render `.wav`** | **File ▸ Export ▸ Audio Mixdown…** (whole project, stereo out) | The acoustic outcome for state→audio experiments |
| **MIDI `.mid`** (if the project has MIDI) | **File ▸ Export ▸ MIDI File…** | Corroborates notes/tempo |

Round-trip import (to sanity-check) is **File ▸ Import ▸ DAWproject**.

> **Caveat (Cubase Artist):** at least one user reported **File ▸ Export ▸
> DAWproject** greyed out on an *audio-only* project on Cubase Artist 15. If it's
> greyed out, add a MIDI/instrument track, or this surface may be gated on your
> edition — tell me and we'll lean on the Track Archive + `.cpr` instead.

### What Cubase's DAWproject export actually carries (important, and honest)

The DAWproject *format* can carry far more than Cubase's *exporter* currently
writes. Grounded in Steinberg docs + staff forum replies + independent testing
(see [EXTERNAL_RESEARCH_LEDGER](EXTERNAL_RESEARCH_LEDGER.md)):

- **Carried:** audio/MIDI/video data, tracks incl. Group Channels & FX channels,
  clips/events, **send settings, fader levels, pan, track colors, routing to
  groups**, tempo/time-signature, and instrument-track *engine identity*.
- **Omitted / unreliable (Cubase 15):** **automation** (roadmap item — often
  missing), **MIDI CC / CC64 sustain, Note Expression, channel strip, crossfades**
  (Steinberg staff confirmed not-yet-supported), time-stretch/slice data.
- **Plug-in state is best-effort, not a guaranteed opaque blob:** the *engine* is
  recreated but its loaded content/settings are frequently lost, and effect
  settings transfer inconsistently.

The importer reflects this: it reads what's present, marks genuinely-opaque
plug-in state as `unknown_state`, and never invents values. So don't be surprised
if a real Cubase export shows **no automation lanes** and **opaque inserts** —
that's Cubase's writer, faithfully reported, not a parser bug.

## Which projects to build (start with these 4)

Minimal set that exercises the parser's hardest paths. Follow the full protocol
in [`CUBASE_FIXTURE_PROTOCOL.md`](CUBASE_FIXTURE_PROTOCOL.md) later.

1. **`p04_dualfilter`** — one audio track named `Gtr`, one insert: **DualFilter**,
   Position set to a distinctive value. (Tests insert identity + the opaque
   plug-in-parameter boundary.)
2. **`p06_send`** — one audio track `Vocal`, one **FX channel** with **REVerence**,
   a send from Vocal → the FX channel at a known level. (Tests sends + routing.)
3. **`p09_folder_group`** — two audio tracks inside a **folder that has its group
   channel enabled**, both routed to that group. (Tests the folder-vs-group
   distinction — the parser path most likely to differ on real output.)
4. **`p12_instrument`** — one **instrument track** (e.g. Retrologue/HALion) with a
   short MIDI part of a few known notes. (Tests instrument track + MIDI.)

## How to hand them to me

Drop the exports anywhere in the repo, one folder per project, e.g.:

```
fixtures/cubase-real/p04_dualfilter/
    p04_dualfilter.dawproject
    p04_dualfilter.xml          # track archive (optional)
    p04_dualfilter.cpr          # optional
    p04_dualfilter_mix.wav      # optional
```

Then tell me the path. I'll run the grounding harness (coming next), which
reports **what parsed, what it didn't understand, and coverage** — and I'll
repair the importer against anything real Cubase does differently. A note of
what you set (e.g. "DualFilter Position = +0.30", "send = −12 dB") lets me check
expected-vs-observed precisely.

## Privacy

These are your own throwaway test projects. Nothing leaves your machine; I only
read the files you point me at. Don't include anything you'd not want in the repo
(and note the real-export folder can be added to `.gitignore` if you prefer they
stay local).
