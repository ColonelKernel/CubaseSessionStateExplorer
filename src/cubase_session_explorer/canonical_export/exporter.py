"""``export_bundle()`` — one input artifact → one v0.2 five-file bundle.

The bundle layout is the adapter contract (pivot plan P1):

    out_dir/
      adapter_descriptor.json   # identity card: what this adapter is
      capabilities.json         # what it can even attempt (per pathway)
      native.json               # verbatim native model (losslessness anchor)
      canonical.snapshot.json   # the flat v0.2 CanonicalDAWSnapshot
      validation.json           # validate_snapshot() report on the wire dict

Dispatch is by input suffix, and each pathway is only as confident as its
surface:

- ``.dawproject`` — the DAWproject extractor (via the repo's single-artifact
  fusion pathway, which adds the observation-model-derived ``unknown_state``
  records and inferred-role backfill on top of ``extractors.dawproject``)
  → native ``SessionState`` → nested mapper → ``flatten_session`` with
  ``OFFICIAL_EXPORT`` stability. The full-strength bundle.
- ``.cpr`` — the conservative cpr_lab string-evidence scan → a DEGRADED but
  honest bundle: one PROJECT entity, availability records saying what could
  not be read, an explicit structural-parse failure, and the full
  ``CprReport`` in ``extensions["cubase"]["cpr_report"]``. Validation passes;
  nothing is fabricated.
- ``.mid`` — the SMF extractor → an evidence-only bundle: PROJECT plus one
  MUSICAL_CONTENT entity per note-carrying MIDI track. Conservative on
  purpose: a MIDI export reveals musical content, not session structure.

Determinism and hygiene: id counters are reset per export; ``created_at``
comes from the input file's mtime (not wall clock); ``snapshot_id`` embeds a
content hash of the input; with ``sanitize=True`` (default) the user's home
directory and the input file's parent directory are scrubbed from every
string in the bundle so frozen fixtures do not leak local filesystem layout.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from typing import Any, Optional

from canonical_snapshot import (
    CanonicalDAWSnapshot,
    DomainCoverage,
    Entity,
    FailureRecord,
    NativeRef,
    ProvenanceRecord,
    Relationship,
    SourceInfo,
    ValidationReport,
    flatten_session,
    validate_snapshot,
)
from canonical_snapshot.ids import reset_id_counters

from .. import __version__ as _pkg_version
from ..extractors import cpr_lab, midi
from ..fusion import ingest as fusion_ingest
from ..ids import reset_ids
from ..utils import sha256_bytes, sha256_file
from .capabilities import (
    ADAPTER_ID,
    build_adapter_descriptor,
    build_capability_manifest,
)
from .mapper import session_state_to_canonical

ADAPTER_NAME = "cubase-session-state-explorer"

BUNDLE_FILES = (
    "adapter_descriptor.json",
    "capabilities.json",
    "native.json",
    "canonical.snapshot.json",
    "validation.json",
)


class ExportError(RuntimeError):
    """The input could not be turned into a bundle at all (loud, never silent)."""


@dataclass
class ExportResult:
    out_dir: str
    files: dict[str, str] = dc_field(default_factory=dict)
    snapshot: Optional[CanonicalDAWSnapshot] = None
    validation: Optional[ValidationReport] = None


def _adapter_version() -> str:
    try:
        from importlib.metadata import version

        return version(ADAPTER_NAME)
    except Exception:
        return _pkg_version


def _created_at(path: str) -> str:
    """Input mtime in UTC — a property of the artifact, not of the export run."""
    return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).isoformat(
        timespec="seconds"
    )


def _snapshot_id(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    return f"cubase:{stem}:{sha256_file(path)[:12]}"


def _sanitize_obj(obj: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(obj, str):
        for needle, sub in replacements:
            if needle and needle in obj:
                obj = obj.replace(needle, sub)
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_obj(v, replacements) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_obj(v, replacements) for v in obj]
    return obj


def _sanitizer(input_path: str, sanitize: bool):
    if not sanitize:
        return lambda obj: obj
    home = os.path.expanduser("~")
    input_dir = os.path.dirname(os.path.abspath(input_path))
    # Longest needle first so the input dir wins over a home-dir prefix.
    replacements = [(input_dir, "."), (home, "~")]

    def _apply(obj: Any) -> Any:
        return _sanitize_obj(obj, replacements)

    return _apply


def _dump_json(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _write_bundle(
    out_dir: str,
    snapshot_dict: dict,
    native_dict: dict,
) -> ExportResult:
    os.makedirs(out_dir, exist_ok=True)
    ver = _adapter_version()
    files: dict[str, str] = {}

    payloads: dict[str, Any] = {
        "adapter_descriptor.json": build_adapter_descriptor(ver).model_dump(),
        "capabilities.json": build_capability_manifest(ver).model_dump(),
        "native.json": native_dict,
        "canonical.snapshot.json": snapshot_dict,
    }
    report = validate_snapshot(snapshot_dict)
    payloads["validation.json"] = report.model_dump()

    for name, payload in payloads.items():
        path = os.path.join(out_dir, name)
        with open(path, "wb") as fh:
            fh.write(_dump_json(payload))
        files[name] = path

    return ExportResult(
        out_dir=out_dir,
        files=files,
        snapshot=CanonicalDAWSnapshot.model_validate(snapshot_dict),
        validation=report,
    )


# ---------------------------------------------------------------------------
# .dawproject — the full-strength pathway
# ---------------------------------------------------------------------------


def _export_dawproject(input_path: str, out_dir: str, sanitize: bool) -> ExportResult:
    result = fusion_ingest(input_path, hash_files=False)
    dawp = result.per_extractor.get("dawproject", {})
    if not dawp.get("ok"):
        raise ExportError(
            f"DAWproject extraction failed for {input_path!r}: "
            f"{dawp.get('warnings') or result.warnings}"
        )
    state = result.session
    session = session_state_to_canonical(state, source_artifact="dawproject")

    clean = _sanitizer(input_path, sanitize)
    native_dict = clean(
        {
            "dialect": "cubase",
            "model_name": session.native.model_name,
            "model": session.native.model,
        }
    )
    native_bytes = _dump_json(native_dict)

    source = SourceInfo(
        daw="cubase",
        daw_version=state.project.cubase_version,
        adapter=ADAPTER_NAME,
        adapter_version=_adapter_version(),
        capture_modes=["dawproject_export"],
    )
    snapshot = flatten_session(
        session,
        source,
        native_file="native.json",
        native_sha256=sha256_bytes(native_bytes),
        snapshot_id=_snapshot_id(input_path),
        created_at=_created_at(input_path),
        default_stability="OFFICIAL_EXPORT",
    )
    snapshot_dict = clean(snapshot.model_dump())
    return _write_bundle(out_dir, snapshot_dict, native_dict)


# ---------------------------------------------------------------------------
# .cpr — degraded but honest
# ---------------------------------------------------------------------------

_CPR_UNREADABLE_DOMAINS = ("structure", "channel", "processing", "routing", "musical_content")


def _export_cpr(input_path: str, out_dir: str, sanitize: bool) -> ExportResult:
    report = cpr_lab.scan(input_path)
    clean = _sanitizer(input_path, sanitize)
    stem = os.path.splitext(os.path.basename(input_path))[0]

    prov_scan = ProvenanceRecord(
        id="prov:0001",
        evidence="OBSERVED",
        capture_method="cpr_evidence_scan",
        source_stability="REVERSE_ENGINEERED",
        source_ref=os.path.basename(input_path),
        explanation=(
            "Conservative string-evidence scan of the proprietary RIFF-like "
            ".cpr binary; file identity and tokens only, never structure."
        ),
    )
    provenance = [prov_scan]

    properties: dict[str, Any] = {
        "file_size": report.file_size,
        "sha256": report.sha256,
        "is_probable_cpr": report.is_probable_cpr,
    }
    prov: dict[str, str] = {"*": prov_scan.id}
    if report.app_version:
        prov_version = ProvenanceRecord(
            id="prov:0002",
            evidence="INFERRED",
            capture_method="cpr_evidence_scan",
            source_stability="REVERSE_ENGINEERED",
            confidence=0.7,
            explanation="Version string matched by regex near a PAppVersion token.",
        )
        provenance.append(prov_version)
        properties["app_version"] = report.app_version
        prov["app_version"] = prov_version.id

    # The session state inside the binary exists but is not recoverable —
    # INACCESSIBLE when the container is recognizably a CPR; if it is not
    # even recognizably a CPR, we do not know what is in there: UNKNOWN.
    status = "INACCESSIBLE" if report.is_probable_cpr else "UNKNOWN"
    availability = {domain: status for domain in _CPR_UNREADABLE_DOMAINS}

    project = Entity(
        id="cubase:project",
        entity_type="PROJECT",
        name=stem,
        properties=properties,
        native=NativeRef(daw="cubase", native_type="cpr_project"),
        prov=prov,
        availability=availability,  # type: ignore[arg-type]
    )

    snapshot = CanonicalDAWSnapshot(
        snapshot_id=_snapshot_id(input_path),
        created_at=_created_at(input_path),
        source=SourceInfo(
            daw="cubase",
            daw_version=report.app_version,
            adapter=ADAPTER_NAME,
            adapter_version=_adapter_version(),
            capture_modes=["cpr_evidence_scan"],
        ),
        project=project.id,
        entities=[project],
        coverage={
            domain: DomainCoverage(applicable=1, unsupported=1)
            for domain in _CPR_UNREADABLE_DOMAINS
        },
        provenance=provenance,
        extensions={
            "cubase": {
                "cpr_report": report.to_dict(),
            }
        },
        warnings=list(report.warnings),
        failures=[
            FailureRecord(
                stage="structural_parse",
                message=(
                    ".cpr structural parsing is UNSUPPORTED: the format is a "
                    "proprietary RIFF-like binary. Only a read-only string-"
                    "evidence scan was performed; every candidate is a "
                    "hypothesis with an offset and a confidence < 1.0."
                ),
                detail=(
                    f"container_tokens={report.container_tokens!r}, "
                    f"plugin_name_candidates="
                    f"{[e.value for e in report.plugin_name_candidates]!r}"
                ),
            )
        ],
    )

    native_dict = clean(
        {"dialect": "cubase", "model_name": "CprReport", "model": report.to_dict()}
    )
    snapshot_dict = clean(snapshot.model_dump())
    snapshot_dict["extensions"]["cubase"]["native_file"] = {
        "path": "native.json",
        "sha256": sha256_bytes(_dump_json(native_dict)),
    }
    return _write_bundle(out_dir, snapshot_dict, native_dict)


# ---------------------------------------------------------------------------
# .mid — evidence-only musical content
# ---------------------------------------------------------------------------


def _export_midi(input_path: str, out_dir: str, sanitize: bool) -> ExportResult:
    result = midi.extract(input_path)
    if not result.ok:
        raise ExportError(
            f"SMF extraction failed for {input_path!r}: {result.warnings}"
        )
    clean = _sanitizer(input_path, sanitize)
    stem = os.path.splitext(os.path.basename(input_path))[0]

    prov_smf = ProvenanceRecord(
        id="prov:0001",
        evidence="OBSERVED",
        capture_method="midi_export",
        source_stability="OFFICIAL_DOCUMENTED",
        source_ref=os.path.basename(input_path),
        explanation="Parsed from a Standard MIDI File (officially documented format).",
    )

    properties: dict[str, Any] = {"smf_division": result.division}
    availability: dict[str, str] = {
        # An SMF carries musical content; session structure, mixer state,
        # processing and routing are simply not representable on this surface.
        "structure": "UNSUPPORTED",
        "channel": "UNSUPPORTED",
        "processing": "UNSUPPORTED",
        "routing": "UNSUPPORTED",
    }
    if result.tempo_bpm is not None:
        properties["tempo"] = result.tempo_bpm
    else:
        availability["tempo"] = "NOT_PRESENT"
    if result.time_signature is not None:
        properties["time_signature"] = result.time_signature
    else:
        availability["time_signature"] = "NOT_PRESENT"

    project = Entity(
        id="cubase:project",
        entity_type="PROJECT",
        name=stem,
        properties=properties,
        native=NativeRef(daw="cubase", native_type="smf_export"),
        prov={"*": prov_smf.id},
        availability=availability,  # type: ignore[arg-type]
    )

    entities = [project]
    relationships: list[Relationship] = []
    observed_tracks = 0
    for i, track in enumerate(result.tracks, start=1):
        if not track.notes:
            continue  # conservative: only claim what demonstrably has content
        observed_tracks += 1
        keys = [n.key for n in track.notes]
        content = Entity(
            id=f"cubase:midi-track-{i}",
            entity_type="MUSICAL_CONTENT",
            name=track.name or f"MIDI track {i}",
            properties={
                "note_count": len(track.notes),
                "channel": track.channel,
                "pitch_min": min(keys),
                "pitch_max": max(keys),
            },
            native=NativeRef(daw="cubase", native_type="smf_track"),
            prov={"*": prov_smf.id},
        )
        entities.append(content)
        relationships.append(
            Relationship(
                id=f"rel:{len(relationships) + 1:04d}",
                rel_type="CONTAINS",
                source=project.id,
                target=content.id,
                properties={"kind": "midi_track"},
                prov_ref=prov_smf.id,
            )
        )

    snapshot = CanonicalDAWSnapshot(
        snapshot_id=_snapshot_id(input_path),
        created_at=_created_at(input_path),
        source=SourceInfo(
            daw="cubase",
            adapter=ADAPTER_NAME,
            adapter_version=_adapter_version(),
            capture_modes=["midi_export"],
        ),
        project=project.id,
        entities=entities,
        relationships=relationships,
        coverage={
            "musical_content": DomainCoverage(
                applicable=observed_tracks, observed=observed_tracks
            ),
            "structure": DomainCoverage(applicable=1, unsupported=1),
            "routing": DomainCoverage(applicable=1, unsupported=1),
            "processing": DomainCoverage(applicable=1, unsupported=1),
        },
        provenance=[prov_smf],
        extensions={"cubase": {}},
        warnings=list(result.warnings),
    )

    native_dict = clean(
        {
            "dialect": "cubase",
            "model_name": "MidiResult",
            "model": {
                "division": result.division,
                "tempo_bpm": result.tempo_bpm,
                "time_signature": result.time_signature,
                "tracks": [
                    {
                        "name": t.name,
                        "channel": t.channel,
                        "notes": [n.model_dump() for n in t.notes],
                    }
                    for t in result.tracks
                ],
                "warnings": result.warnings,
            },
        }
    )
    snapshot_dict = clean(snapshot.model_dump())
    snapshot_dict["extensions"]["cubase"]["native_file"] = {
        "path": "native.json",
        "sha256": sha256_bytes(_dump_json(native_dict)),
    }
    return _write_bundle(out_dir, snapshot_dict, native_dict)


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

_DISPATCH = {
    ".dawproject": _export_dawproject,
    ".cpr": _export_cpr,
    ".npr": _export_cpr,  # Nuendo sibling, same container family
    ".mid": _export_midi,
    ".midi": _export_midi,
}


def export_bundle(input_path: str, out_dir: str, *, sanitize: bool = True) -> ExportResult:
    """Export one input artifact as a five-file canonical bundle.

    Raises :class:`ExportError` for unsupported suffixes or unreadable
    inputs — an adapter fails loudly, it never fabricates a snapshot.
    """
    suffix = os.path.splitext(input_path)[1].lower()
    handler = _DISPATCH.get(suffix)
    if handler is None:
        raise ExportError(
            f"Unsupported input suffix {suffix!r} for canonical export; "
            f"supported: {sorted(_DISPATCH)}"
        )
    if not os.path.exists(input_path):
        raise ExportError(f"Input not found: {input_path!r}")
    # Deterministic ids for both id systems involved: the repo's native
    # stable-id fallback counters and the shared canonical-snapshot counters.
    reset_ids()
    reset_id_counters()
    return handler(input_path, out_dir, sanitize)
