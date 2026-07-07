"""The Cubase adapter's capability manifest and descriptor.

What this adapter can even *attempt* to observe, per pathway — kept separate
from what any one capture actually yielded (that is the snapshot's
``coverage``). Honesty rules applied here:

- ``validation_status`` is ``TESTED`` only where a fixture test in this repo
  demonstrably exercises the value (``tests/test_dawproject_extractor.py``,
  ``tests/test_diff_and_experiment.py``, ``tests/test_extractors_and_
  robustness.py``, ``tests/test_canonical_export.py``); everything else stays
  ``UNTESTED``/``CLAIMED``.
- The fixtures are *synthetic* DAWproject files modeled on Cubase 15 exports
  (``tools/make_fixtures.py``), not files produced by a running Cubase — so
  ``tested_daw_version`` stays unset and the caveat is stated in ``notes``.
- ``read`` / ``write`` / ``live_observation`` / ``render`` are separate: the
  MIDI Remote bridge under ``runtime/`` exists but has never been validated
  against a live Cubase here, so its claims are minimal and UNTESTED.
"""

from __future__ import annotations

from canonical_snapshot.capabilities import (
    AdapterDescriptor,
    CapabilityManifest,
    DomainCapability,
    FieldCapability,
)

ADAPTER_ID = "cubase-hybrid"
DAW = "cubase"


def _field(
    support: str,
    capture: str,
    stability: str,
    status: str = "UNTESTED",
) -> FieldCapability:
    return FieldCapability(
        applicability="APPLICABLE",
        support=support,  # type: ignore[arg-type]
        capture_method=capture,
        source_stability=stability,  # type: ignore[arg-type]
        validation_status=status,  # type: ignore[arg-type]
    )


def build_capability_manifest(adapter_version: str) -> CapabilityManifest:
    dawp = "dawproject_export"
    cpr = "cpr_evidence_scan"
    smf = "midi_export"

    read = {
        "structure": DomainCapability(
            fields={
                "track_name": _field("FULL", dawp, "OFFICIAL_EXPORT", "TESTED"),
                "track_type": _field("FULL", dawp, "OFFICIAL_EXPORT", "TESTED"),
                "hierarchy": _field("PARTIAL", dawp, "OFFICIAL_EXPORT", "TESTED"),
                "folder_group_channel": _field("PARTIAL", dawp, "OFFICIAL_EXPORT"),
                "track_color": _field("PARTIAL", dawp, "OFFICIAL_EXPORT"),
            }
        ),
        "channel": DomainCapability(
            fields={
                "volume": _field("FULL", dawp, "OFFICIAL_EXPORT", "TESTED"),
                "pan": _field("FULL", dawp, "OFFICIAL_EXPORT", "TESTED"),
                "mute": _field("FULL", dawp, "OFFICIAL_EXPORT", "TESTED"),
                "solo": _field("PARTIAL", dawp, "OFFICIAL_EXPORT"),
            }
        ),
        "processing": DomainCapability(
            fields={
                "insert_identity": _field("FULL", dawp, "OFFICIAL_EXPORT", "TESTED"),
                "insert_order": _field("FULL", dawp, "OFFICIAL_EXPORT", "TESTED"),
                "bypass_state": _field("PARTIAL", dawp, "OFFICIAL_EXPORT"),
                "plugin_preset": _field("PARTIAL", dawp, "OFFICIAL_EXPORT"),
                # Built-in devices that enumerate host-visible <Parameters>
                # are read; third-party plug-in state stays an opaque blob and
                # is explicitly flagged unavailable. Both halves are tested.
                "plugin_parameters": _field("PARTIAL", dawp, "OFFICIAL_EXPORT", "TESTED"),
            }
        ),
        "routing": DomainCapability(
            fields={
                "output_routing": _field("FULL", dawp, "OFFICIAL_EXPORT", "TESTED"),
                "sends": _field("FULL", dawp, "OFFICIAL_EXPORT", "TESTED"),
                "send_levels": _field("FULL", dawp, "OFFICIAL_EXPORT", "TESTED"),
                "input_routing": _field("NONE", dawp, "OFFICIAL_EXPORT"),
            }
        ),
        "musical_content": DomainCapability(
            fields={
                "tempo": _field("FULL", dawp, "OFFICIAL_EXPORT", "TESTED"),
                "time_signature": _field("FULL", dawp, "OFFICIAL_EXPORT", "TESTED"),
                "midi_notes": _field("FULL", dawp, "OFFICIAL_EXPORT", "TESTED"),
                "automation": _field("PARTIAL", dawp, "OFFICIAL_EXPORT", "TESTED"),
                "markers": _field("PARTIAL", dawp, "OFFICIAL_EXPORT"),
            }
        ),
        # The binary .cpr: a conservative string-evidence scan, never a
        # structural parse. Evidence-only by design.
        "cpr_evidence": DomainCapability(
            fields={
                "container_identification": _field(
                    "PARTIAL", cpr, "REVERSE_ENGINEERED", "TESTED"
                ),
                "plugin_name_candidates": _field(
                    "PARTIAL", cpr, "REVERSE_ENGINEERED", "TESTED"
                ),
                "app_version": _field("PARTIAL", cpr, "REVERSE_ENGINEERED"),
                "structural_state": _field("NONE", cpr, "REVERSE_ENGINEERED"),
            }
        ),
        # Standard MIDI File export: an officially documented format.
        "midi_content": DomainCapability(
            fields={
                "midi_notes": _field("FULL", smf, "OFFICIAL_DOCUMENTED", "TESTED"),
                "tempo": _field("FULL", smf, "OFFICIAL_DOCUMENTED", "TESTED"),
                "time_signature": _field("PARTIAL", smf, "OFFICIAL_DOCUMENTED"),
                "track_names": _field("PARTIAL", smf, "OFFICIAL_DOCUMENTED"),
            }
        ),
    }

    # runtime/bridge_listener.py + runtime/cubase-state-probe.js exist (MIDI
    # Remote API bridge) but have never been validated against a running
    # Cubase in this repo: minimal, UNTESTED/CLAIMED claims only.
    live_observation = {
        "channel": DomainCapability(
            fields={
                "volume": _field("PARTIAL", "midi_remote_bridge", "SUPPORTED_INTEGRATION"),
                "pan": _field("PARTIAL", "midi_remote_bridge", "SUPPORTED_INTEGRATION"),
                "mute": _field("PARTIAL", "midi_remote_bridge", "SUPPORTED_INTEGRATION"),
                "solo": _field("PARTIAL", "midi_remote_bridge", "SUPPORTED_INTEGRATION"),
            }
        ),
        "transport": DomainCapability(
            fields={
                "tempo": FieldCapability(
                    applicability="APPLICABLE",
                    support="PARTIAL",
                    capture_method="midi_remote_bridge",
                    source_stability="SUPPORTED_INTEGRATION",
                    validation_status="CLAIMED",
                ),
            }
        ),
    }

    return CapabilityManifest(
        daw=DAW,
        adapter=ADAPTER_ID,
        adapter_version=adapter_version,
        read=read,
        write={},  # no write pathway exists, and none is claimed
        live_observation=live_observation,
        render={},  # rendering is done by Cubase itself, outside this adapter
        notes=[
            "Fixtures are synthetic DAWproject files modeled on Cubase 15 "
            "exports (tools/make_fixtures.py); no capability has been "
            "validated against a file produced by a running Cubase instance, "
            "so tested_daw_version is deliberately unset.",
            "DAWproject is an open interchange format that Cubase 14/15 "
            "import and export (OFFICIAL_EXPORT stability).",
            ".cpr support is a read-only string-evidence scan "
            "(REVERSE_ENGINEERED); it never claims structural state.",
            "The MIDI Remote runtime bridge (runtime/) observes the selected "
            "channel + transport only; it is UNTESTED against a live Cubase "
            "in this repo.",
        ],
    )


def build_adapter_descriptor(adapter_version: str) -> AdapterDescriptor:
    return AdapterDescriptor(
        adapter_id=ADAPTER_ID,
        daw=DAW,
        capture_modes=["dawproject_export", "cpr_evidence_scan", "midi_export"],
        read=(
            "DAWproject export: structure, channels, routing, sends, "
            "processing identity/order, automation, MIDI notes (OFFICIAL_"
            "EXPORT). .cpr: string-evidence scan only (REVERSE_ENGINEERED). "
            ".mid: musical content (OFFICIAL_DOCUMENTED)."
        ),
        write="NONE",
        live_observation=(
            "MINIMAL: MIDI Remote bridge exists (runtime/) for selected-"
            "channel mixer state + transport; UNTESTED in this repo."
        ),
        render="NONE (renders are produced by Cubase, outside the adapter)",
        known_limitations=[
            "Third-party plug-in parameter values are opaque (DAWproject "
            "state blobs; binary .cpr); only built-in devices that enumerate "
            "host-visible <Parameters> are readable.",
            ".cpr structural parsing is unsupported; only string evidence "
            "with offsets and calibrated confidence is emitted.",
            "Input/monitoring routing is not carried by any export surface.",
            "Markers, chords and the full tempo map are not read from "
            "DAWproject yet (initial tempo/time-signature only).",
        ],
    )
