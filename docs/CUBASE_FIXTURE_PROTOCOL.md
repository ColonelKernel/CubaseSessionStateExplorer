# Cubase Fixture Protocol

Reverse engineering without controlled experiments is guesswork. This protocol
defines controlled projects with **known expected state**, so extraction can be
validated (expected vs. observed — see `VALIDATION.md`).

## Two fixture tiers

1. **Synthetic, self-contained (implemented).** Because DAWproject is an open
   MIT format that Cubase imports/exports, `tools/make_fixtures.py` authors
   *valid* `.dawproject` files directly, plus matching WAV renders and a MIDI
   file. This lets the whole pipeline run and be validated **without a Cubase
   install**, deterministically, in CI. These are the fixtures the tests use.
2. **Cubase-authored (manual protocol below).** For grounding against the real
   application: create each project in Cubase 15, save the `.cpr`, export a
   `.dawproject` and a Track Archive, bounce a render, hash every artifact, and
   diff against adjacent versions. These validate the *real* surfaces and feed
   `cpr-lab` binary diffs. (Not committed; requires a licensed Cubase.)

## Project set

| ID | Project | Controlled variable | Validates |
|---|---|---|---|
| 00 | Empty | sample rate, tempo, time sig | project metadata |
| 01 | One audio track | distinctive name + color | track name/color |
| 02 | One audio event | known start + duration | clip timing |
| 03 | Mixer state | fader, pan, mute, solo | channel state |
| 04 | One insert | one stock FX (e.g. **DualFilter**), known params | insert identity |
| 05 | Two inserts (+ reversed) | order DualFilter→MonoDelay vs reversed | insert order |
| 06 | Send routing | audio → FX channel, known send amount | sends |
| 07 | Group routing | two tracks → group | group routing |
| 08 | Folder only | folder + children, no summing | organizational folder |
| 09 | Folder w/ group channel | folder group enabled, children summed | folder-vs-group distinction |
| 10 | Volume automation | three known points | automation events |
| 11 | Plug-in automation | one stock param automated | parameter automation |
| 12 | Instrument track | one instrument, several known notes | instrument + MIDI |
| 13 | Tempo map | multiple tempo events | tempo map |
| 14 | Markers | position + cycle marker | markers |
| 15 | Chord track | several chord events | chord events |
| 16 | Score state | small passage + one notation change | representational state |

## Stock plug-ins as validation instruments

Prefer simple, well-documented Steinberg stock effects with easy-to-identify
parameters: **DualFilter** (Position, Resonance), **MonoDelay** (Delay,
Feedback, Mix), **DJ-EQ** (Lo/Mid/Hi gain), **Limiter** (Input, Release,
Output). Build fixtures where *exactly one parameter changes* to enable binary
comparison, preset comparison, runtime observation, state-diff and audio-delta
validation.

## Implemented fixtures (from `make_fixtures.py`)

| File | Encodes |
|---|---|
| `demo_session.dawproject` | 4 audio + group + FX channel + master + instrument+MIDI + automation (full graph/UI demo) |
| `dualfilter_a/b.dawproject` | **P04/P11** DualFilter Position −0.5→+0.5 (as a 1-point automation lane, the observable channel) |
| `dualfilter_a/b.wav` | matching renders (bright/dark filtered tone) |
| `routing_a/b.dawproject` | **P06** dry vocal vs vocal + reverb send |
| `routing_a/b.wav` | matching renders (dry vs reverb tail) |
| `notes.mid` | **P12** MIDI performance |
| `manifest.json` | expected-value manifest per fixture |

> Note on P04/P11: DAWproject stores plug-in *parameter values* in an opaque
> blob, so a raw "DualFilter Position" dial is not enumerable. We therefore
> encode the controlled change as a **plug-in-parameter automation point**,
> which *is* first-class in DAWproject — an honest, observable proxy for the
> intended intervention. The Cubase-authored tier (P04) would additionally
> exercise the preset surface.

## Manifest schema

```json
{
  "fixture_id": "project_04_insert_dualfilter",
  "cubase_version": "15.x",
  "expected_changes": [{"field": "track[0].inserts[0].plugin", "value": "DualFilter"}],
  "artifacts": {"cpr": "...", "dawproject": "...", "audio": "...", "midi": null}
}
```
