# CPR Reverse-Engineering Notebook

A scientific, conservative log of hypotheses about the binary `.cpr` format.
**Rules:** never write to `.cpr`; never bypass protection; never promote a
string-proximity guess to a structural fact; every hypothesis carries supporting
and contradicting evidence and a confidence. The implementation of this posture
is `extractors/cpr_lab.py` (read-only evidence scanner + CLI `cpr-lab`).

## Why evidence-only

Public evidence (file-signature databases; omeriko9's viewer for Cubase 2/3)
establishes that `.cpr` is **RIFF-like** — tagged chunks with recurring tokens
(`NUNDROOT`, `CmObject`, `PAppVersion`, `RIFF`, `ROOT`) and embedded UTF-8/UTF-16
strings. That is enough to extract *evidence* (names, versions, plug-in
candidates), but **not** enough to safely reconstruct structure for modern
Cubase 15. A speculative full parser would be fragile and dishonest. So `cpr-lab`
stops at evidence and defers structure to the open DAWproject surface.

---

## HYPOTHESIS CPR-001 — Container identity via magic tokens
**Claim.** A file is a Cubase/Nuendo project iff it contains `NUNDROOT`,
`CmObject`, or `PAppVersion` near the header.
**Evidence.** Signature databases list these tokens; `cpr_lab.scan` finds them
and sets `is_probable_cpr`. Synthetic test (`test_cpr_lab_detects_tokens`)
confirms detection.
**Counter-evidence.** Newer/compressed variants may relocate or compress tokens.
**Confidence.** 0.9 for classic layouts. **Status.** Implemented (container_token evidence).

## HYPOTHESIS CPR-002 — Track/plug-in names as recoverable strings
**Claim.** Track names and stock plug-in names appear as UTF-8/UTF-16 runs.
**Evidence.** omeriko9 recovered track names; `cpr_lab` emits plug-in-name
candidates (substring match against a stock list) with offsets.
**Counter-evidence.** The same string may appear in unrelated metadata/paths;
proximity ≠ structural ownership. Third-party names are unbounded.
**Confidence.** 0.5 (name present), **0.3** (name→specific track). **Status.**
Useful for evidence + `cpr-lab diff`; insufficient for structural parsing.

## HYPOTHESIS CPR-003 — App version near `PAppVersion`
**Claim.** A `MAJOR.MINOR(.PATCH)` string sits near `PAppVersion`.
**Evidence.** Token present in databases; regex in `cpr_lab` extracts it and it
backfills `project.cubase_version` when DAWproject lacks it.
**Counter-evidence.** Regex may match an unrelated version string.
**Confidence.** 0.7. **Status.** Implemented, low-weight.

## HYPOTHESIS CPR-004 — Controlled single edits localize to string deltas
**Claim.** Changing one thing in Cubase and re-saving changes a small set of
strings/bytes; `cpr-lab diff` attributes the edit to appearing/disappearing
strings + size delta.
**Evidence.** The `diff()` method computes size delta + string-set differences.
Requires the Cubase-authored fixture tier (P04/P05) to exercise fully.
**Counter-evidence.** Binary re-serialization can perturb unrelated regions
(timestamps, offsets), so byte-level diffs are noisy.
**Confidence.** 0.4 as attribution; higher as a *hint*. **Status.** Tool ready;
awaits real controlled `.cpr` pairs.

## HYPOTHESIS CPR-005 — Chunked structure is decodable
**Claim.** The RIFF chunk tree can be walked into typed records.
**Evidence.** RIFF-like tokens suggest chunking.
**Counter-evidence.** No published modern schema; nested `CmObject` semantics
unknown; risk of overfitting to one project.
**Confidence.** 0.2. **Status.** **Not attempted.** Deferred to DAWproject; would
require many controlled fixtures + Steinberg cooperation to do responsibly.

---

## What `cpr-lab` provides today
```
cpr-lab scan   project.cpr     # container tokens, version, string counts, candidates
cpr-lab strings project.cpr    # plug-in-name candidates with offsets + confidence
cpr-lab diff   a.cpr b.cpr     # size + string-set delta of a controlled edit
```
All output carries offsets and sub-1.0 confidence. Fusion ingests CPR results as
low-confidence corroboration only (e.g. version backfill, plug-in-name hints),
never as structural truth.
