# External Research Ledger

Every external source consulted for the Cubase access surfaces, classified by
authority. **We do not present community inference as Steinberg specification.**
Inspected 2026-07-05.

## Tier 1 — Official Steinberg documentation

| Source | URL | What it establishes | Use |
|---|---|---|---|
| MIDI Remote API — Programmer's Guide | https://steinbergmedia.github.io/midiremote_api_doc/ | The API is ES5 JS; `mHostAccess` exposes selected `mMixerChannel` (name, volume, pan, mute, solo), transport, mixer bank, Quick Controls; binding via callbacks. | Grounds `runtime/cubase-state-probe.js` + the capability report. |
| MIDI Remote API (Cubase Pro 14 manual) | https://www.steinberg.help/r/cubase-pro/14.0/en/cubase_nuendo/topics/midi_remote/midi_remote_api_c.html | Same, version-anchored to current Cubase. | Capability matrix `midi_remote` column. |
| Importing DAWproject Files (Cubase Pro Help) | steinberg.help (Cubase Pro) | Cubase 14 imports/exports DAWproject. | Confirms DAWproject as a first-party Cubase surface. |
| DAWproject in Steinberg products | https://helpcenter.steinberg.de/hc/en-us/articles/25142209226642 | DAWproject supported in **Cubase 14**, Cubasis 3.7.1, VST Live 2.2. | Justifies DAWproject as primary extractor. |
| About Project Files (Cubase manual) | archive.steinberg.help | `.cpr` is the project format; audio not embedded (referenced). | CPR posture: references, not media. |
| Exporting Tracks as Track Archives | archive.steinberg.help | Track Archive is a single `.xml` track export. | Grounds `track_archive.py`. |

## Tier 2 — Official / open specifications & code

| Source | URL | Licence | What it establishes | Use |
|---|---|---|---|---|
| **DAWproject** spec + reference impl | https://github.com/bitwig/dawproject | **MIT** | ZIP(`project.xml`,`metadata.xml`,`audio/`,`plugins/`); `<Project><Application><Transport><Structure><Track><Channel><Devices><Vst3Plugin><State path><Sends><Send><Arrangement><Lanes><Clips><Clip><Notes><Note><Points>`; plug-in state stored as opaque blob. | Directly grounds `extractors/dawproject.py` and the fixture generator (which authors valid DAWproject files). |
| DAWproject FAQ (Bitwig) | https://www.bitwig.com/support/technical_support/dawproject-file-format-faqs-62/ | doc | Format is plain XML/ZIP, open & free, carries audio/MIDI/automation/plug-in + structure. | Confirms scope + openness. |
| "Cubase 14 Now Supports DAWproject" (Bitwig) | https://www.bitwig.com/stories/cubase-14-now-supports-dawproject-341/ | doc | Cross-vendor interop confirmation. | Motivation. |
| VST3 SDK (referenced, not vendored) | https://github.com/steinbergmedia/vst3sdk | proprietary/dual | `IEditController` parameter model: stable ids, normalized 0–1, host visibility, automation flags. | Informs `DeviceParameterState` fields + the `StateProbe` design note. **Not redistributed.** |

## Tier 3 — Community code (patterns, not specification)

| Source | URL | Licence | Relevance | Reuse? |
|---|---|---|---|---|
| steinbergmedia/midiremote-userscripts | https://github.com/steinbergmedia/midiremote-userscripts | (repo terms) | Official sample user scripts — canonical MIDI Remote idioms (`makeDeviceDriver`, `mHostAccess`, page mapping). | Patterns only; informed probe structure. |
| bjoluc/cubase-mcu-midiremote | https://github.com/bjoluc/cubase-mcu-midiremote | MIT | Serious MCU controller scripts; shows mixer-bank binding, surface value idioms at scale. | Confirms bankable channel observation is real; no code copied. |
| Dre Dyson — MIDI Remote feedback-loop guide | dredyson.com | blog | Demonstrates a virtual-MIDI feedback loop reading `mTrackSelection` — exactly the bridge pattern. | Confirms MIDI-only bridge is viable (no fs/network in the API). |
| Pettor/plugin-cubase-midi-remote-extensions; THK-artjom/cubase_midiremote_atom; pederbacher/cubase-midiremote-userscripts | GitHub | various | Additional community MIDI Remote implementations. | Corroborate the capability boundary; not inspected line-by-line. |

## Tier 4 — Experimental reverse-engineering (community, low authority)

| Source | URL | What it claims | Our stance |
|---|---|---|---|
| omeriko9/Cubase-Project-File-Reverse-Engineering | https://github.com/omeriko9/Cubase-Project-File-Reverse-Engineering | `.cpr` viewer/editor for Cubase 2/3; tracks/names identifiable in the binary. | Corroborates the RIFF-like, tokenized, string-bearing nature of `.cpr`. Informs `cpr_lab.py` **evidence** approach only. We do **not** adopt structural claims for modern Cubase. |
| File-signature databases (fileinfo, filext, justsolve) | various | `.cpr` is RIFF-based; tokens `NUNDROOT`, `CmObject`, `PAppVersion`, `RIFF`, `ROOT`; a common signature `EC CE 00 01 …`. | Used only to seed `cpr_lab` container-token detection; each hit is emitted as low-confidence evidence with an offset. |
| Steinberg forum: "Track Archive XML Specification"; "Legality of reverse-engineering Cubase formats" | forums.steinberg.net | Community discussion; no official Track Archive schema published. | We treat Track Archive parsing as cautious/heuristic and stay within user-owned-file inspection. |

## Addendum (2026-07-05): DAWproject spec + Cubase-writer grounding

Deep-dive to harden the importer against the *real* spec (drove the parser fixes
for channel-role enums, standalone `<Channel>`, built-in devices, `<Send><Enable>`,
`<Points><Target>`, normalized velocity, unit-aware pan).

**Tier 2 — spec (authoritative structure):**
- `Project.xsd`, `MetaData.xsd` — raw: raw.githubusercontent.com/bitwig/dawproject/main/Project.xsd — exact elements, attributes, enums (`Unit`, `TimeUnit`, `MixerRole=regular|master|effect|submix|vca`, `DeviceRole=instrument|noteFX|audioFX|analyzer`, `SendType=pre|post`, `Interpolation=hold|linear`).
- `DoubleAdapter.java`, `Note.java` (same repo) — confirm `vel`/`rel` are **normalized 0..1** and `inf` is a literal double token.
- Real Bitwig 5.0 example `project.xml` embedded in the repo README — used verbatim in `tests/test_real_dawproject.py`.

**Tier 1 — official Steinberg (Cubase-writer behavior):**
- Cubase 15 DAWproject export doc — steinberg.help/r/cubase-pro/15.0/.../exporting_dawproject_files_t.html (scope: "video, audio, and MIDI data").
- Staff (Martin Jirsak): MIDI CC/Note Expression/channel strip/crossfade **not supported** — forums.steinberg.net/t/…/1023436; greyed-out export probe — /t/…/1030398.
- Track Archive export/import docs — steinberg.help Cubase Pro 14/15.

**Tier 3 — independent testing (community):**
- Sound on Sound (Cubase↔Cubasis) and polarity.me (Cubase↔Bitwig): sends/fader/pan/routing carried; **automation & plug-in content unreliable**; round-trips lossy. Treated as community evidence, not spec.

> Bitwig marketing ("preserves … automation … third-party plug-in state") describes the **format's** ceiling, NOT Cubase's behavior — not cited as Cubase capability.

## Addendum (2026-07-06): VST3 preset file format (local SDK)

The `.vstpreset` parser (`extractors/vstpreset.py`) is grounded in the **official
VST3 SDK on local disk** (`/Volumes/Mac-Storage/GitHub/vst3sdk`):

- `public.sdk/source/vst/vstpresetfile.h` — the format is documented in the
  header comment: `'VST3'` magic + int32 version + 32-byte ASCII class id
  (component/processor FUID) + int64 chunk-list offset; `'List'` + int32 count +
  entries of (4-byte id, int64 offset, int64 size).
- `vstpresetfile.cpp` — chunk ids `Comp`/`Cont`/`Prog`/`Info`/`List`,
  `kFormatVersion = 1`, header size 48.
- `pluginterfaces/vst/vstpresetkeys.h` — MediaBay Info attributes
  (`PlugInName`, `PlugInCategory`, `Name`, `FileName`, `StateType`, ...).

**License posture:** no SDK code is copied or redistributed; only the factual
file-format constants (magic strings, field sizes/offsets, attribute keys) are
implemented, in original Python. The `Comp`/`Cont` payloads are each plug-in's
own opaque serialization — the parser fingerprints them and never claims to
decode parameter values generically.

## Legal constraints observed

- No Steinberg proprietary code (incl. the VST3 SDK) is redistributed in this repo.
- No DRM/encryption/copy-protection is bypassed; `cpr_lab` is strictly read-only.
- DAWproject is MIT; fixtures authored in it are original works.
- Community reverse-engineering is cited as *evidence/corroboration*, never
  promoted to "specification". Confidence and provenance travel with every value.
