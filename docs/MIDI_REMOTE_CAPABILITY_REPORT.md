# MIDI Remote Capability Report

**Do not assume the MIDI Remote API is a full session API.** It is a
*control-surface* API. This report states what it can and cannot observe, based
on the official Programmer's Guide and the community implementations in the
ledger, and describes the empirical capability probe.

## What the API is

- **Language:** ES5 JavaScript, loaded by Cubase as a MIDI Remote driver script.
- **Host access:** `mHostAccess` exposes the **selected** mixer channel
  (`mTrackSelection.mMixerChannel`), the **transport**, a **mixer bank** of
  channels, and per-channel **Quick Controls** (8 generic, host-mapped plug-in
  parameters). Values are delivered via `mOnProcessValueChange` / `mOnTitleChange`
  callbacks and are normalized (0–1) or titles.
- **No filesystem or network.** The script cannot write files or open sockets;
  the only egress is MIDI. Our bridge therefore mirrors observed values as
  CC/SysEx to a virtual port that `runtime/bridge_listener.py` decodes.

## Capability boundary (bindable ⇒ observable)

| Host value | Observable? | Notes |
|---|---|---|
| selected channel **name** | ✅ | `mOnTitleChange` |
| selected channel **volume** | ✅ | normalized fader |
| selected channel **pan** | ✅ | |
| selected channel **mute / solo** | ✅ | |
| **transport** tempo / playing | ✅ | |
| **mixer bank** channel names/values | ✅ (banked) | one bank at a time |
| **Quick Controls** (8) | ◐ | only the 8 generic host-exposed params, per channel |
| track **selection** changes | ✅ | drives which channel is observed |
| arbitrary **VST3 parameters** | ❌ | not exposed beyond Quick Controls |
| **insert slot** enumeration | ❌ | not exposed |
| **sends / routing** topology | ❌ | not exposed |
| **folder / group** structure | ❌ | not exposed |
| **automation** curves | ❌ | not exposed |

## The empirical probe (do not infer capability from naming)

`runtime/cubase-state-probe.js` binds each host value and records a `CAPS` flag
that flips **only when a value actually fires** — the capability matrix is
therefore *measured at runtime*, not assumed from API method names. Findings are
echoed into the snapshot's `capability` block and surfaced by the fusion layer.

## Bridge & fusion

```
Cubase  --(select channels)-->  cubase-state-probe.js
   |  mirrors CC(7,10,20,21,22,40..47) + SysEx(name) on a virtual MIDI port
   v
bridge_listener.py  -->  runtime/snapshot.json  { transport, channels[], capability{} }
   v
extractors/runtime.py  -->  partial SessionState (fields marked `observed`)
   v
fusion._overlay_runtime  -->  upgrades mute/solo/volume/pan to observed ground
                              truth on name-matched tracks; records `conflicting`
                              provenance when a static export disagrees.
```

Without a live Cubase, `python runtime/bridge_listener.py --template` writes a
representative snapshot so the runtime path is exercisable offline (used
conceptually by `test_extractors_and_robustness.py`).

## Research consequence

The MIDI Remote API's value here is precisely its **narrowness**: it provides
*ground-truth* mixer observations for a handful of fields that the file surfaces
cannot verify (mute/solo especially), and its Quick Controls give a small,
honest window onto plug-in parameters — exactly the fields the capability matrix
marks `U`/opaque elsewhere. It is a corroboration and conflict-detection surface,
not a project model.
