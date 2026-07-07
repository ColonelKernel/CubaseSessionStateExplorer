"""Evidence fusion: bundle artifacts -> one normalized SessionState.

Strategy (v0, deliberately simple and auditable):

1. Choose a **structural base** from the strongest available structured source,
   in priority order: DAWproject > Track Archive. This gives tracks, routing,
   devices, automation, clips.
2. **Overlay** runtime (MIDI Remote) observations: for channels whose name
   matches a base track, upgrade mixer fields (volume/pan/mute/solo) to
   ``observed`` ground truth, recording a conflict if they disagree.
3. **Attach** CPR evidence and MIDI notes as corroboration / extra content.
4. **Derive** ``unknown_state`` from the declarative observation model for every
   canonical field no available artifact reveals.
5. **Compute** an explainable coverage percent.

Nothing is fabricated: if only a CPR is present, the base is an evidence-only
session with almost everything in ``unknown_state``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import observation_model as om
from .bundle import Bundle, discover
from .extractors import cpr_lab, dawproject, midi, musicxml, runtime, track_archive, vstpreset
from .ids import stable_id
from .models import (
    ProjectMeta,
    SessionState,
    UnknownState,
)
from .provenance import Provenance, conflicting_note, observed, parsed


@dataclass
class FusionResult:
    session: SessionState
    warnings: list[str] = field(default_factory=list)
    per_extractor: dict = field(default_factory=dict)


_STRUCTURAL_PRIORITY = ["dawproject", "track_archive"]


def ingest(path: str, hash_files: bool = True) -> FusionResult:
    """Discover a bundle at ``path`` and fuse it into a SessionState."""
    bundle = discover(path, hash_files=hash_files)
    return fuse(bundle)


def fuse(bundle: Bundle) -> FusionResult:
    warnings = list(bundle.warnings)
    per_extractor: dict = {}
    artifact_types = bundle.types()

    base: Optional[SessionState] = None

    # 1. structural base -----------------------------------------------------
    for art in bundle.of_type("dawproject"):
        res = dawproject.extract(art.path)
        per_extractor["dawproject"] = {"ok": res.ok, "warnings": res.warnings}
        warnings.extend(res.warnings)
        if res.ok and res.session is not None:
            base = res.session
            break

    if base is None:
        for art in bundle.of_type("track_archive"):
            res = track_archive.extract(art.path)
            per_extractor["track_archive"] = {"ok": res.ok, "warnings": res.warnings}
            warnings.extend(res.warnings)
            if res.ok and res.session is not None:
                base = res.session
                break

    if base is None:
        # evidence-only session (CPR / runtime / nothing structural)
        base = SessionState(project=ProjectMeta(project_name=_bundle_name(bundle)))
        base.provenance = parsed("fusion", confidence=0.0,
                                 explanation="No structured source; evidence-only session.")

    # 2. runtime overlay -----------------------------------------------------
    for art in bundle.of_type("midi_remote"):
        res = runtime.extract(art.path)
        per_extractor["runtime"] = {"ok": res.ok, "warnings": res.warnings,
                                    "capability": res.capability}
        warnings.extend(res.warnings)
        if res.ok and res.session is not None:
            _overlay_runtime(base, res.session)
            if "midi_remote" not in base.capture.artifacts:
                base.capture.artifacts.append("midi_remote")

    # 3a. CPR evidence -------------------------------------------------------
    for art in bundle.of_type("cpr"):
        rep = cpr_lab.scan(art.path)
        per_extractor["cpr"] = {"is_probable_cpr": rep.is_probable_cpr,
                                "plugin_candidates": [e.value for e in rep.plugin_name_candidates],
                                "app_version": rep.app_version}
        base.capture.artifacts.append("cpr")
        base.metadata.setdefault("cpr_evidence", {})[art.path.rsplit("/", 1)[-1]] = {
            "is_probable_cpr": rep.is_probable_cpr,
            "plugin_name_candidates": [e.value for e in rep.plugin_name_candidates],
            "app_version": rep.app_version,
            "container_tokens": rep.container_tokens,
        }
        if rep.app_version and not base.project.cubase_version:
            base.project.cubase_version = rep.app_version

    # 3b. MIDI content -------------------------------------------------------
    midi_results: list["midi.MidiResult"] = []
    for art in bundle.of_type("midi_export"):
        res = midi.extract(art.path)
        per_extractor["midi"] = {"ok": res.ok, "n_tracks": len(res.tracks)}
        warnings.extend(res.warnings)
        if res.ok:
            midi_results.append(res)
            _attach_midi(base, res)
            base.capture.artifacts.append("midi_export")

    # 3c. MusicXML notation layer (representational state) -------------------
    for art in bundle.of_type("musicxml_export"):
        res = musicxml.extract(art.path)
        per_extractor["musicxml"] = {
            "ok": res.ok,
            "n_parts": len(res.score.parts),
            "n_pitched_notes": len(res.score.pitched_notes),
        }
        warnings.extend(res.warnings)
        if res.ok:
            base.score_state = res.score
            base.capture.artifacts.append("musicxml_export")
            # Performed vs. notated: relate the MIDI layer to its notation.
            # Performed notes come from structural clips when present, else
            # directly from the MIDI export (which may not match a track name).
            perf = [n for t in base.all_tracks() for c in t.clips for n in c.notes]
            if not perf:
                perf = [n for mres in midi_results for mt in mres.tracks
                        for n in mt.notes]
            if perf and res.score.pitched_notes:
                base.metadata["score_vs_performance"] = \
                    musicxml.compare_performance_to_score(perf, res.score)

    # 3d. Preset evidence (plug-in identity + opaque-state fingerprints) -----
    for art in bundle.of_type("preset"):
        res = vstpreset.extract(art.path)
        per_extractor.setdefault("preset", []).append(
            {"path": art.path, "ok": res.ok, "plugin_name": res.plugin_name,
             "class_id": res.class_id})
        warnings.extend(res.warnings)
        if not res.ok:
            continue
        base.capture.artifacts.append("preset")
        base.metadata.setdefault("preset_evidence", []).append(res.to_dict())
        _attach_preset(base, res)

    for art in bundle.of_type("rendered_audio"):
        base.capture.artifacts.append("rendered_audio")
        base.metadata.setdefault("renders", []).append(art.path)

    # 4. derive unknown_state from the observation model --------------------
    base.capture.artifacts = sorted(set(base.capture.artifacts))
    _derive_unknown_state(base)

    # 4b. backfill explorer-side heuristics (role/family), marked inferred ---
    from .models import backfill_heuristics
    backfill_heuristics(base)

    # 5. coverage ------------------------------------------------------------
    cov = om.coverage(base.capture.artifacts)
    base.capture.coverage_percent = cov["coverage_percent"]
    base.metadata["coverage"] = cov
    base.warnings.extend(w for w in warnings if w not in base.warnings)

    return FusionResult(session=base, warnings=warnings, per_extractor=per_extractor)


def _bundle_name(bundle: Bundle) -> str:
    return bundle.root.rstrip("/").rsplit("/", 1)[-1] or "cubase-session"


def _overlay_runtime(base: SessionState, rt: SessionState) -> None:
    """Upgrade mixer fields to observed ground truth where names match."""
    by_name = {t.name.lower(): t for t in base.all_tracks()}
    for rt_track in rt.tracks:
        target = by_name.get(rt_track.name.lower())
        if target is None:
            # Runtime saw a channel the structural source lacks; add it.
            base.tracks.append(rt_track)
            continue
        for f in ("volume_db", "pan", "mute", "solo"):
            rt_val = getattr(rt_track, f)
            if rt_val is None:
                continue
            base_val = getattr(target, f)
            if base_val is not None and _differs(base_val, rt_val):
                # Genuine conflict: keep the runtime (observed-at-capture) value,
                # but mark it conflicting and keep the midi_remote source visible.
                prov = conflicting_note(
                    f"structural source said {base_val!r}, runtime observed "
                    f"{rt_val!r}; keeping observed runtime value.",
                )
                prov.source.type = "midi_remote"
                target.field_provenance[f] = prov
            else:
                target.field_provenance[f] = observed("midi_remote")
            setattr(target, f, rt_val)


def _differs(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) > 1e-3
    except (TypeError, ValueError):
        return a != b


def _attach_midi(base: SessionState, res: "midi.MidiResult") -> None:
    if base.tempo is None and res.tempo_bpm:
        base.tempo = res.tempo_bpm
    if base.time_signature is None and res.time_signature:
        base.time_signature = res.time_signature
    by_name = {t.name.lower(): t for t in base.all_tracks()}
    for mt in res.tracks:
        if not mt.notes:
            continue
        target = by_name.get((mt.name or "").lower())
        note_count = len(mt.notes)
        if target is not None:
            base.metadata.setdefault("midi_note_counts", {})[target.name] = note_count
            # If the structural clip had no notes, enrich the first midi clip.
            for clip in target.clips:
                if clip.clip_type == "midi" and not clip.notes:
                    clip.notes = mt.notes
                    clip.midi_note_count = note_count
                    break
        else:
            base.metadata.setdefault("orphan_midi_tracks", {})[mt.name or "?"] = note_count


def _attach_preset(base: SessionState, res: "vstpreset.VstPresetResult") -> None:
    """Enrich matching devices with preset evidence.

    Match order: VST3 class id (strong — the processor FUID), then plug-in
    name (weaker, case-insensitive). Enrichment: ``preset_name``, class id,
    and the Comp/Cont state fingerprints — identity and *detectability* of the
    opaque state, never fabricated parameter values.
    """
    comp = res.chunk("Comp")
    cont = res.chunk("Cont")
    matched = False
    for dev in base.all_devices():
        by_class = bool(res.class_id and dev.plugin_identifier
                        and dev.plugin_identifier.upper() == res.class_id)
        by_name = bool(res.plugin_name
                       and dev.name.lower() == res.plugin_name.lower())
        if not (by_class or by_name):
            continue
        matched = True
        if res.preset_name and not dev.preset_name:
            dev.preset_name = res.preset_name
            dev.field_provenance["preset_name"] = parsed(
                "preset", confidence=0.9 if by_class else 0.7,
                explanation=("matched by VST3 class id" if by_class
                             else f"matched by plug-in name '{res.plugin_name}'"),
                artifact=res.path.rsplit("/", 1)[-1],
            )
        native = dev.native.setdefault("cubase", {})
        native["vstpreset_class_id"] = res.class_id
        if comp and comp.sha16:
            native["vstpreset_comp_sha"] = comp.sha16
        if cont and cont.sha16:
            native["vstpreset_cont_sha"] = cont.sha16
    if not matched:
        base.warnings.append(
            f"Preset '{res.plugin_name or res.path.rsplit('/', 1)[-1]}' did not "
            "match any device in the structural session; kept as evidence only.")


def _derive_unknown_state(session: SessionState) -> None:
    """Create UnknownState records for canonical fields nothing reveals."""
    hidden = om.hidden_fields(session.capture.artifacts)
    session.unknown_state = []  # rebuild deterministically
    for field_name in sorted(hidden):
        info = om.STATE_GAP_INFO.get(field_name, {})
        session.unknown_state.append(
            UnknownState(
                id=stable_id("gap", field_name),
                entity_id=None,
                state_gap=field_name,
                reason=str(info.get("reason",
                          f"No available artifact reveals '{field_name}'.")),
                potential_sources=list(info.get("potential_sources", [])),
                severity="notable" if field_name in (
                    "plugin_parameters", "automation_events", "output_routing"
                ) else "info",
            )
        )
    # Per-device parameter gaps (concrete, entity-anchored) when structural
    # source could not read parameter values.
    if "plugin_parameters" in hidden:
        for dev in session.all_devices():
            if not dev.parameters:
                session.unknown_state.append(
                    UnknownState(
                        id=stable_id("gap", "param", dev.id),
                        entity_id=dev.id,
                        state_gap="insert_parameter_state",
                        reason=f"Parameter values for '{dev.name}' are not exposed "
                               "by the available surfaces (opaque plug-in state).",
                        potential_sources=["VST preset export", "MIDI Remote Quick Controls"],
                        severity="notable",
                    )
                )
