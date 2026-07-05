"""Canonical Cubase session-state schema.

This is a *dual-layer, lossless, provenance-carrying* model:

* **Layer 1 — canonical / cross-DAW.** Field names deliberately match the
  REAPER/Ableton/Logic prototypes (``ProjectState``, ``TrackState``,
  ``DeviceState``, ``SendState`` ...) so a Cubase session can sit in the same
  graph, diff, and dataset as the other DAWs. This is the portability layer.

* **Layer 2 — Cubase-native extensions.** Anything Cubase exposes that the
  cross-DAW vocabulary would *flatten* lives under ``native.cubase`` (e.g.
  ``group_channel_enabled`` on a folder, ``freeze_state`` on a track). Nothing
  Cubase tells us is ever dropped.

Every entity carries a :class:`~cubase_session_explorer.provenance.Provenance`
so the UI can always answer "where did this value come from, and how sure are
we?". ``UnknownState`` records make the *observability boundary* itself a
first-class, queryable object — a core research artifact.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field

from .provenance import Provenance, observed

SCHEMA_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Mixins
# ---------------------------------------------------------------------------

class Provenanced(BaseModel):
    """Base for entities that carry provenance + a Cubase-native side-channel."""

    # Entity-level provenance (a field whose origin differs gets its own entry
    # in ``field_provenance`` keyed by field name).
    provenance: Provenance = Field(default_factory=lambda: observed())
    field_provenance: dict[str, Provenance] = Field(default_factory=dict)
    # Cubase-native extensions: {"cubase": {...}}. Preserves semantics the
    # cross-DAW layer would otherwise flatten. Never interpreted by portable code.
    native: dict[str, Any] = Field(default_factory=dict)
    # Verbatim unparsed evidence, for auditability / future re-parsing.
    raw_source: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Devices / plug-ins
# ---------------------------------------------------------------------------

class DeviceParameterState(Provenanced):
    id: str
    device_id: str
    name: str
    value: Union[float, str, bool, None] = None
    normalized_value: Optional[float] = None  # VST3 0..1 host-facing value
    unit: Optional[str] = None
    is_automated: Optional[bool] = None
    is_visible_to_host: Optional[bool] = None


class DeviceState(Provenanced):
    id: str
    track_id: str
    index: int = 0                       # insert slot order (0-based)
    name: str
    vendor: Optional[str] = None
    plugin_identifier: Optional[str] = None   # VST3 class id / CLAP id when known
    plugin_format: Optional[Literal["VST3", "VST2", "AU", "internal", "unknown"]] = None
    device_type: Optional[str] = None    # audio_effect | instrument | channel_strip
    device_family: Optional[str] = None  # EQ | Dynamics | Ambience | ...
    enabled: Optional[bool] = True       # inverse of bypass
    bypassed: Optional[bool] = None
    preset_name: Optional[str] = None
    state_blob_ref: Optional[str] = None  # bundle path to opaque plug-in state chunk
    latency_samples: Optional[int] = None
    parameters: list[DeviceParameterState] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Events (audio / MIDI)  — Cubase: Audio Events, MIDI Parts
# ---------------------------------------------------------------------------

class MidiNote(BaseModel):
    time_beats: float
    duration_beats: float
    key: int             # 0..127
    velocity: int        # 0..127
    channel: int = 0
    release_velocity: Optional[int] = None


class ClipState(Provenanced):
    id: str
    track_id: str
    name: str
    clip_type: Literal["audio", "midi"] = "audio"
    start_time_beats: Optional[float] = None
    length_beats: Optional[float] = None
    start_time_seconds: Optional[float] = None
    length_seconds: Optional[float] = None
    loop_start_beats: Optional[float] = None
    loop_end_beats: Optional[float] = None
    audio_file: Optional[str] = None
    media_id: Optional[str] = None
    midi_note_count: Optional[int] = None
    notes: list[MidiNote] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

class SendState(Provenanced):
    id: str
    source_track_id: str
    target_return_id: str          # FX channel / group id
    send_name: Optional[str] = None
    level_db: Optional[float] = None
    pan: Optional[float] = None
    enabled: Optional[bool] = True
    pre_fader: Optional[bool] = None   # None => unrecoverable from this surface


class RouteState(Provenanced):
    """A main output-routing edge (track -> group/bus/output), distinct from a send."""

    id: str
    source_track_id: str
    target_id: str                 # group / output-bus id
    route_type: Literal["output", "input", "sidechain", "instrument_out"] = "output"


# ---------------------------------------------------------------------------
# Automation  — PARAMETER -> LANE -> TIMED EVENTS -> CURVE
# ---------------------------------------------------------------------------

class AutomationPoint(BaseModel):
    time_beats: float
    value: float                    # normalized 0..1 where applicable
    curve: Literal["linear", "step", "ramp", "spline", "unknown"] = "linear"


class AutomationLane(Provenanced):
    id: str
    track_id: str
    # What is being automated. ``device_id`` set => plug-in parameter automation.
    parameter_name: str
    device_id: Optional[str] = None
    parameter_id: Optional[str] = None
    read_enabled: Optional[bool] = None
    write_enabled: Optional[bool] = None
    muted: Optional[bool] = None
    unit: Optional[str] = None
    points: list[AutomationPoint] = Field(default_factory=list)

    @property
    def point_count(self) -> int:
        return len(self.points)


# ---------------------------------------------------------------------------
# Tracks / channels
# ---------------------------------------------------------------------------

TrackType = Literal[
    "audio", "midi", "instrument", "group", "fx", "folder",
    "marker", "chord", "ruler", "video", "automation", "master",
]


class TrackState(Provenanced):
    id: str
    index: int = 0
    name: str
    track_type: TrackType = "audio"
    role: Optional[str] = None          # heuristic: Vocal/Drums/Bass/... (inferred)
    color: Optional[str] = None
    parent_id: Optional[str] = None     # folder containment
    volume_db: Optional[float] = None
    pan: Optional[float] = None
    mute: Optional[bool] = None
    solo: Optional[bool] = None
    record_enabled: Optional[bool] = None
    monitor: Optional[bool] = None
    frozen: Optional[bool] = None
    channel_config: Optional[str] = None   # "mono" | "stereo" | ...
    output_target_id: Optional[str] = None
    clips: list[ClipState] = Field(default_factory=list)
    devices: list[DeviceState] = Field(default_factory=list)
    sends: list[SendState] = Field(default_factory=list)


class FolderState(Provenanced):
    """A Cubase folder track. The organizational-only vs group-channel-enabled
    distinction is *semantic* and must not be collapsed (see native.cubase)."""

    id: str
    name: str
    index: int = 0
    child_track_ids: list[str] = Field(default_factory=list)
    organizational_only: bool = True
    group_channel_enabled: bool = False   # True => children can be summed here


# ---------------------------------------------------------------------------
# Musical structure  (Cubase's rich higher-level layer)
# ---------------------------------------------------------------------------

class TempoEvent(BaseModel):
    time_beats: float
    bpm: float
    curve: Literal["jump", "ramp"] = "jump"


class TimeSignatureEvent(BaseModel):
    time_beats: float
    numerator: int
    denominator: int


class Marker(BaseModel):
    id: str
    time_beats: float
    name: Optional[str] = None
    cycle: bool = False             # True => cycle/range marker
    end_time_beats: Optional[float] = None


class ChordEvent(BaseModel):
    time_beats: float
    root: Optional[str] = None      # e.g. "C", "F#"
    quality: Optional[str] = None   # e.g. "maj7", "min"
    bass: Optional[str] = None
    scale: Optional[str] = None


class MusicalStructure(BaseModel):
    tempo_map: list[TempoEvent] = Field(default_factory=list)
    time_signatures: list[TimeSignatureEvent] = Field(default_factory=list)
    markers: list[Marker] = Field(default_factory=list)
    chords: list[ChordEvent] = Field(default_factory=list)


class ScoreState(BaseModel):
    """Notated / interpreted layer — distinct from performed MIDI. Deliberately
    minimal in v0; the schema exists so score interpretation can be added
    without a migration. Same performance can yield many notations."""

    present: bool = False
    layouts: list[dict[str, Any]] = Field(default_factory=list)
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Media & unknown state
# ---------------------------------------------------------------------------

class MediaFile(BaseModel):
    id: str
    path: str
    kind: Literal["audio", "video", "midi", "unknown"] = "audio"
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    duration_seconds: Optional[float] = None
    exists: Optional[bool] = None


class UnknownState(BaseModel):
    """A first-class record of what we could NOT observe. The observability
    boundary is itself a research object (compare across DAWs)."""

    id: str
    entity_id: Optional[str] = None
    state_gap: str                       # e.g. "insert_parameter_state"
    reason: str
    potential_sources: list[str] = Field(default_factory=list)
    severity: Literal["info", "notable", "blocking"] = "notable"


# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------

class ProjectMeta(BaseModel):
    project_name: str
    project_path: Optional[str] = None
    daw_dialect: str = "cubase"
    cubase_version: Optional[str] = None
    sample_rate: Optional[int] = None
    frame_rate: Optional[str] = None
    record_format: Optional[str] = None
    project_start_seconds: Optional[float] = None
    project_length_seconds: Optional[float] = None
    left_locator_beats: Optional[float] = None
    right_locator_beats: Optional[float] = None
    tempo_mode: Optional[Literal["fixed", "track"]] = None


class CaptureInfo(BaseModel):
    """Provenance of the whole extraction: which artifacts fed this snapshot."""

    artifacts: list[str] = Field(default_factory=list)
    extractors_run: list[str] = Field(default_factory=list)
    coverage_percent: Optional[float] = None
    captured_at: Optional[str] = None


class SessionState(Provenanced):
    """The normalized Cubase session — the canonical output of the adapter."""

    schema_version: str = SCHEMA_VERSION
    adapter: dict[str, str] = Field(
        default_factory=lambda: {"daw": "cubase", "adapter_version": "0.1.0"}
    )
    project: ProjectMeta
    tempo: Optional[float] = None            # convenience: fixed/initial tempo
    time_signature: Optional[str] = None     # convenience: initial "4/4"
    tracks: list[TrackState] = Field(default_factory=list)
    folders: list[FolderState] = Field(default_factory=list)
    return_tracks: list[TrackState] = Field(default_factory=list)  # FX channels
    groups: list[TrackState] = Field(default_factory=list)
    master_track: Optional[TrackState] = None
    routes: list[RouteState] = Field(default_factory=list)
    automation: list[AutomationLane] = Field(default_factory=list)
    musical_structure: MusicalStructure = Field(default_factory=MusicalStructure)
    score_state: ScoreState = Field(default_factory=ScoreState)
    media: list[MediaFile] = Field(default_factory=list)
    unknown_state: list[UnknownState] = Field(default_factory=list)
    capture: CaptureInfo = Field(default_factory=CaptureInfo)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # -- convenience accessors ------------------------------------------

    def all_tracks(self) -> list[TrackState]:
        out = list(self.tracks) + list(self.groups) + list(self.return_tracks)
        if self.master_track:
            out.append(self.master_track)
        return out

    def all_devices(self) -> list[DeviceState]:
        return [d for t in self.all_tracks() for d in t.devices]

    def all_sends(self) -> list[SendState]:
        return [s for t in self.all_tracks() for s in t.sends]

    def track_by_id(self, track_id: str) -> Optional[TrackState]:
        for t in self.all_tracks():
            if t.id == track_id:
                return t
        return None

    def device_by_id(self, device_id: str) -> Optional[DeviceState]:
        for d in self.all_devices():
            if d.id == device_id:
                return d
        return None


# ---------------------------------------------------------------------------
# Analysis-side models (audio linkage, interventions)
# ---------------------------------------------------------------------------

class AudioDescriptorSet(BaseModel):
    id: str
    source_id: str                              # snapshot/render id
    source_type: str = "mixdown"                # mixdown | stem | track | clip
    file_path: Optional[str] = None
    duration_seconds: Optional[float] = None
    sample_rate: Optional[int] = None
    rms_mean: Optional[float] = None
    rms_std: Optional[float] = None
    peak_amplitude: Optional[float] = None
    crest_factor_db: Optional[float] = None
    spectral_centroid_mean: Optional[float] = None
    spectral_bandwidth_mean: Optional[float] = None
    spectral_rolloff_mean: Optional[float] = None
    zero_crossing_rate_mean: Optional[float] = None
    onset_rate_hz: Optional[float] = None
    stereo_width_proxy: Optional[float] = None
    dynamic_range_db: Optional[float] = None
    integrated_loudness_lufs: Optional[float] = None
    mfcc_means: list[float] = Field(default_factory=list)
    available: bool = True
    warnings: list[str] = Field(default_factory=list)


def backfill_heuristics(session: SessionState) -> SessionState:
    """Fill explorer-side heuristics (track role, device family) that are NOT
    DAW facts. Explicit values are never overwritten; backfilled ones are
    marked ``inferred`` so the UI can distinguish guesses from observations.
    Idempotent and safe to run after fusion or after validation."""
    from .provenance import inferred
    from .utils import classify_device_family, classify_track_role

    for track in session.all_tracks():
        if track.role is None and track.name:
            role = classify_track_role(track.name)
            if role and role != "Unknown":
                track.role = role
                track.field_provenance["role"] = inferred(
                    f"role '{role}' guessed from track name '{track.name}'",
                    confidence=0.55,
                )
        for device in track.devices:
            if device.device_family is None and device.name:
                fam = classify_device_family(device.name)
                if fam and fam != "Unknown":
                    device.device_family = fam
                    device.field_provenance["device_family"] = inferred(
                        f"family '{fam}' guessed from plug-in name '{device.name}'",
                        confidence=0.6,
                    )
    return session


def validate_session_dict(payload: dict[str, Any]) -> SessionState:
    """Validate a raw dict into a SessionState and backfill heuristics."""
    return backfill_heuristics(SessionState.model_validate(payload))
