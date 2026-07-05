"""Declarative Cubase observability model.

Generalizes the Logic prototype's observation model to Cubase's *many* evidence
surfaces. It is a table, not code: for each artifact type it declares which
canonical state fields it ``reveals`` (direct, high confidence), ``constrains``
(supports inference), ``asserts`` (user claims), or ``hides`` (not recoverable).

Two things are derived from this table, so they can never drift from it:

1. the per-field **coverage / confidence** a fused session should carry, and
2. the **UnknownState** records for everything no available artifact reveals.

This makes the *observability boundary* — arguably the most research-relevant
output of the whole project — auditable and comparable across DAWs.
"""

from __future__ import annotations

from typing import Iterable

# Canonical state fields we reason about (a superset spanning all DAWs).
SESSION_STATE_FIELDS: tuple[str, ...] = (
    "project_metadata",
    "tempo",
    "tempo_map",
    "time_signatures",
    "markers",
    "chords",
    "track_name",
    "track_type",
    "track_color",
    "hierarchy",
    "folder_group_channel",
    "role",
    "mute_solo",
    "record_enable",
    "volume",
    "pan",
    "input_routing",
    "output_routing",
    "sends",
    "send_levels",
    "insert_identity",
    "insert_order",
    "bypass_state",
    "plugin_parameters",
    "plugin_preset",
    "automation_lanes",
    "automation_events",
    "audio_events",
    "audio_file_refs",
    "midi_parts",
    "midi_notes",
    "vst_instruments",
    "notation_state",
    "rendered_audio",
)

# artifact_type -> {reveals|constrains|asserts|hides: [fields]}
OBSERVATION_MODEL: dict[str, dict[str, list[str]]] = {
    "dawproject": {
        # Cubase 14+/15 open XML/ZIP export. The strongest structured surface.
        "reveals": [
            "project_metadata", "tempo", "time_signatures", "track_name",
            "track_type", "track_color", "hierarchy", "output_routing",
            "sends", "send_levels", "volume", "pan", "mute_solo",
            "insert_identity", "insert_order", "bypass_state",
            "automation_lanes", "automation_events", "audio_events",
            "audio_file_refs", "midi_parts", "midi_notes", "vst_instruments",
        ],
        "constrains": ["role", "folder_group_channel", "plugin_preset"],
        # DAWproject stores plug-in state as an opaque blob (State path=...),
        # so individual parameter *values* are NOT reliably enumerable.
        "hides": ["plugin_parameters", "notation_state", "record_enable",
                  "input_routing", "markers", "chords", "tempo_map"],
    },
    "track_archive": {
        # Cubase Track Archive .xml — class-attributed generic XML.
        "reveals": ["track_name", "track_type", "hierarchy", "audio_events",
                    "midi_parts", "insert_identity"],
        "constrains": ["role", "insert_order", "output_routing", "sends"],
        "hides": ["plugin_parameters", "automation_events", "rendered_audio",
                  "notation_state"],
    },
    "cpr": {
        # Binary RIFF project. Evidence scan only — strings, not structure.
        "reveals": [],
        "constrains": ["track_name", "insert_identity", "plugin_preset",
                       "project_metadata"],
        "hides": ["plugin_parameters", "automation_events", "sends",
                  "output_routing", "midi_notes"],
    },
    "midi_export": {
        "reveals": ["midi_notes", "midi_parts", "tempo", "time_signatures",
                    "track_name"],
        "constrains": ["role", "tempo_map"],
        "hides": ["plugin_parameters", "insert_identity", "sends",
                  "output_routing", "rendered_audio"],
    },
    "musicxml_export": {
        "reveals": ["notation_state", "time_signatures", "track_name"],
        "constrains": ["role"],
        "hides": ["plugin_parameters", "sends", "output_routing",
                  "audio_events", "rendered_audio"],
    },
    "midi_remote": {
        # Runtime capture via the MIDI Remote API. Observes the *selected*
        # channel + transport; NOT the full project model.
        "reveals": ["volume", "pan", "mute_solo", "tempo"],
        "constrains": ["track_name", "plugin_parameters"],  # Quick Controls only
        "hides": ["insert_order", "automation_events", "sends",
                  "output_routing", "hierarchy"],
    },
    "preset": {
        # Track / VST / FX-chain preset files.
        "reveals": ["insert_identity", "plugin_preset"],
        "constrains": ["insert_order", "plugin_parameters", "volume", "pan"],
        "hides": ["automation_events", "sends", "rendered_audio"],
    },
    "rendered_audio": {
        "reveals": ["rendered_audio"],
        "constrains": [],
        "hides": [f for f in SESSION_STATE_FIELDS if f != "rendered_audio"],
    },
    "manual_annotation": {
        "asserts": ["role", "insert_identity", "sends", "output_routing",
                    "plugin_parameters"],
        "hides": [],
    },
}

# Human-readable consequence text for gaps (why the gap matters + how to lift it).
STATE_GAP_INFO: dict[str, dict[str, object]] = {
    "plugin_parameters": {
        "reason": "DAWproject stores plug-in state as an opaque blob; the CPR "
                  "binary does not expose parameters; third-party VST3 params "
                  "are not enumerable without loading the plug-in.",
        "consequence": "We know a plug-in is present and its preset name, but "
                       "not its dial settings — so we cannot relate a parameter "
                       "change to the render from files alone.",
        "potential_sources": ["VST preset export", "track preset export",
                              "MIDI Remote Quick Controls", "custom VST3 StateProbe"],
    },
    "automation_events": {
        "reason": "Only DAWproject exposes automation points; without it the "
                  "curve is baked into audio but not editable state.",
        "consequence": "Time-varying mix moves are audible but not recoverable "
                       "as structured state.",
        "potential_sources": ["DAWproject export", "MIDI CC export"],
    },
    "output_routing": {
        "reason": "Routing is only reliably present in DAWproject; the CPR "
                  "binary and audio stems do not expose it.",
        "consequence": "The bus/group topology may be flattened.",
        "potential_sources": ["DAWproject export", "Track Archive export"],
    },
    "notation_state": {
        "reason": "Score interpretation lives only in MusicXML / the project; "
                  "the same MIDI performance yields many notations.",
        "consequence": "Representational (notation) state is separated from "
                       "acoustically-active state — by design in v0.",
        "potential_sources": ["MusicXML export", "Dorico export"],
    },
    "input_routing": {
        "reason": "Input/monitoring routing is not carried by any external export.",
        "consequence": "Record-time signal path is unknown.",
        "potential_sources": ["MIDI Remote runtime", "custom Cubase extension"],
    },
}


def fields_revealed_by(artifacts: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for art in artifacts:
        spec = OBSERVATION_MODEL.get(art, {})
        out.update(spec.get("reveals", []))
        out.update(spec.get("asserts", []))
    return out


def fields_constrained_by(artifacts: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for art in artifacts:
        out.update(OBSERVATION_MODEL.get(art, {}).get("constrains", []))
    return out


def hidden_fields(artifacts: Iterable[str]) -> set[str]:
    """Fields no available artifact reveals or asserts (the gap set)."""
    seen = set(artifacts)
    revealed = fields_revealed_by(seen)
    constrained = fields_constrained_by(seen)
    return set(SESSION_STATE_FIELDS) - revealed - constrained


def coverage(artifacts: Iterable[str]) -> dict[str, object]:
    """Explainable coverage metric over the canonical field set."""
    seen = list(artifacts)
    revealed = fields_revealed_by(seen)
    constrained = fields_constrained_by(seen) - revealed
    hidden = set(SESSION_STATE_FIELDS) - revealed - constrained
    total = len(SESSION_STATE_FIELDS)
    # Revealed counts full, constrained counts half.
    score = (len(revealed) + 0.5 * len(constrained)) / total
    return {
        "coverage_percent": round(100.0 * score, 1),
        "revealed": sorted(revealed),
        "constrained": sorted(constrained),
        "hidden": sorted(hidden),
        "n_fields": total,
    }
