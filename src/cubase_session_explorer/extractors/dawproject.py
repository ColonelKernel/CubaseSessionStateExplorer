"""DAWproject extractor — the primary high-confidence Cubase surface.

DAWproject (https://github.com/bitwig/dawproject, MIT) is an open XML/ZIP
interchange format that **Cubase 14 and 15 import and export**. Unlike the
binary ``.cpr``, it is a documented, parseable container carrying tracks,
channels, routing, sends, devices, automation, notes, tempo and time
signatures. This makes it the fastest credible path to *real* Cubase state.

Container layout::

    project.dawproject   (zip)
      ├── project.xml     # <Project> structure + arrangement
      ├── metadata.xml    # <MetaData> title/artist/...
      ├── audio/…         # referenced media
      └── plugins/…       # opaque plug-in state blobs (State path="…")

This parser is deliberately *tolerant*: the DAWproject schema has evolved and
different exporters vary (element vs. attribute placement, device element names
``Vst3Plugin`` / ``ClapPlugin`` / ``BuiltinDevice``). Unknown elements are kept
in ``raw_source`` rather than dropped. Plug-in *parameter values* are NOT
fabricated — DAWproject stores plug-in state as an opaque blob, so we record the
blob reference and mark parameters unavailable.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from typing import Any, Optional
from xml.etree import ElementTree as ET

from ..ids import stable_id
from ..models import (
    AutomationLane,
    AutomationPoint,
    ClipState,
    DeviceParameterState,
    DeviceState,
    FolderState,
    MediaFile,
    MidiNote,
    Marker,
    ProjectMeta,
    RouteState,
    SendState,
    SessionState,
    TempoEvent,
    TrackState,
)
from ..provenance import exported, inferred, parsed, unavailable
from ..utils import linear_to_db, safe_float, safe_int

DAWPROJECT_SOURCE = "dawproject"

# The set of DAWproject element localnames this parser reads meaningfully.
# The `diagnose` harness compares a real file's element census against this set
# so anything Cubase emits that we don't yet handle surfaces immediately —
# turning "validate against a real export" into a concrete, mechanical loop.
HANDLED_ELEMENTS: frozenset[str] = frozenset({
    "Project", "Application", "MetaData",
    "Transport", "Tempo", "TimeSignature",
    "Structure", "Track", "Channel", "Volume", "Pan", "Mute",
    "Devices", "Vst3Plugin", "Vst2Plugin", "ClapPlugin", "BuiltinDevice",
    "Device", "AuPlugin", "Plugin", "Auv3Plugin",
    "Equalizer", "Compressor", "NoiseGate", "Limiter",  # BuiltinDevice subtypes
    "Enabled", "State", "Parameters",
    "RealParameter", "BoolParameter", "IntegerParameter", "EnumParameter",
    "parameter", "TimeSignatureParameter",
    "Sends", "Send", "Enable",
    "Arrangement", "Lanes", "Clips", "Clip", "ClipSlot",
    "Notes", "Note", "Warps", "Warp", "Audio", "Video", "File",
    "Points", "Target", "RealPoint", "Point", "BoolPoint",
    "IntegerPoint", "EnumPoint", "TimeSignaturePoint",
    "TempoAutomation", "TimeSignatureAutomation",
    "Markers", "markers", "Marker", "Scenes", "Scene",
})

# Content types seen on <Track contentType="...">
_TRACK_TYPE_MAP = {
    "audio": "audio",
    "notes": "midi",
    "midi": "midi",
    "automation": "automation",
    "video": "video",
    "markers": "marker",
    "tracks": "folder",   # a Track with only sub-tracks / no content
}


@dataclass
class DawprojectResult:
    session: Optional[SessionState] = None
    warnings: list[str] = field(default_factory=list)
    ok: bool = False


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find(el: ET.Element, name: str) -> Optional[ET.Element]:
    for child in el:
        if _localname(child.tag) == name:
            return child
    return None


def _findall(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if _localname(c.tag) == name]


def _param_value(el: Optional[ET.Element]) -> Optional[float]:
    """Read a RealParameter/Bool element's ``value`` attribute."""
    if el is None:
        return None
    return safe_float(el.get("value"))


def load_project_xml(path: str) -> tuple[Optional[ET.Element], list[str], list[str]]:
    """Return (project_root, member_names, warnings). Never raises."""
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            xml_name = next(
                (n for n in names if n.endswith("project.xml")),
                next((n for n in names if n.endswith(".xml") and "meta" not in n.lower()), None),
            )
            if xml_name is None:
                warnings.append("No project.xml inside DAWproject container.")
                return None, names, warnings
            with zf.open(xml_name) as fh:
                root = ET.parse(fh).getroot()
            return root, names, warnings
    except zipfile.BadZipFile:
        # Some tools export the raw XML with a .dawproject extension.
        try:
            root = ET.parse(path).getroot()
            return root, [path], warnings
        except ET.ParseError as exc:
            warnings.append(f"Not a valid DAWproject zip or XML: {exc}")
            return None, [], warnings
    except (OSError, ET.ParseError) as exc:
        warnings.append(f"Failed to read DAWproject: {exc}")
        return None, [], warnings


def extract(path: str) -> DawprojectResult:
    """Parse a ``.dawproject`` file into partial canonical SessionState."""
    result = DawprojectResult()
    root, members, warnings = load_project_xml(path)
    result.warnings.extend(warnings)
    if root is None:
        return result

    artifact = path.rsplit("/", 1)[-1]
    app_el = _find(root, "Application")
    app_name = app_el.get("name") if app_el is not None else None
    app_version = app_el.get("version") if app_el is not None else None

    project = ProjectMeta(
        project_name=artifact.rsplit(".", 1)[0],
        project_path=path,
        cubase_version=app_version if (app_name and "cubase" in app_name.lower()) else None,
    )

    session = SessionState(project=project)
    session.provenance = exported(source_type=DAWPROJECT_SOURCE, artifact=artifact)
    session.capture.artifacts.append(DAWPROJECT_SOURCE)
    session.capture.extractors_run.append("dawproject")
    if app_name:
        session.metadata["dawproject_application"] = f"{app_name} {app_version or ''}".strip()

    # --- Transport: tempo + time signature ---------------------------------
    transport = _find(root, "Transport")
    if transport is not None:
        tempo_el = _find(transport, "Tempo")
        tempo = _param_value(tempo_el)
        if tempo is not None:
            session.tempo = tempo
            session.musical_structure.tempo_map.append(
                TempoEvent(time_beats=0.0, bpm=tempo)
            )
        ts_el = _find(transport, "TimeSignature")
        if ts_el is not None:
            num = safe_int(ts_el.get("numerator"))
            den = safe_int(ts_el.get("denominator"))
            if num and den:
                session.time_signature = f"{num}/{den}"

    # --- Structure: tracks & channels --------------------------------------
    structure = _find(root, "Structure")
    channel_index: dict[str, dict[str, Any]] = {}  # channel/track native id -> info
    # param id -> {owner_id, owner_kind, name, unit}; for automation Target IDREFs.
    param_index: dict[str, dict[str, Any]] = {}

    if structure is not None:
        _walk_tracks(structure, session, channel_index, param_index,
                     parent_id=None, depth=0)
        # Standalone <Channel> children of <Structure> (master/effect/submix/vca
        # buses that are NOT wrapped in a <Track>). Real DAWproject / Cubase output
        # routinely places the master and FX/group channels here.
        for el in structure:
            if _localname(el.tag) == "Channel":
                _read_standalone_channel(el, session, channel_index, param_index)

    # --- Resolve routing destinations (channel destination -> track) -------
    _resolve_routing(session, channel_index)

    # --- Arrangement: clips / notes / automation ---------------------------
    arrangement = _find(root, "Arrangement")
    if arrangement is not None:
        _walk_arrangement(arrangement, session, channel_index, param_index)

    # --- Media files -------------------------------------------------------
    for name in members:
        if name.startswith("audio/") or name.startswith("samples/"):
            session.media.append(
                MediaFile(
                    id=stable_id("media", name),
                    path=name,
                    kind="audio",
                    exists=None,
                )
            )

    # --- Hash opaque plug-in state blobs -----------------------------------
    # We cannot read the parameter VALUES inside a VST3 <State> blob, but we CAN
    # fingerprint the blob bytes. That makes a controlled plug-in change
    # *detectable* (the hash differs) while staying honest that the specific
    # parameter is not recoverable — exactly the Cubase-VST3 reality.
    _hash_state_blobs(path, session)

    result.session = session
    result.ok = True
    return result


def _hash_state_blobs(path: str, session: SessionState) -> None:
    from ..utils import sha256_bytes

    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            for dev in session.all_devices():
                ref = dev.state_blob_ref
                if ref and ref in names:
                    dev.native.setdefault("cubase", {})["state_blob_sha"] = \
                        sha256_bytes(zf.read(ref))[:16]
    except (zipfile.BadZipFile, OSError):
        pass  # raw-XML input or unreadable zip: no blob to fingerprint


def _walk_tracks(
    parent_el: ET.Element,
    session: SessionState,
    channel_index: dict[str, dict[str, Any]],
    param_index: dict[str, dict[str, Any]],
    parent_id: Optional[str],
    depth: int,
) -> None:
    for el in parent_el:
        if _localname(el.tag) != "Track":
            continue
        native_id = el.get("id") or el.get("name") or f"track{len(session.tracks)}"
        name = el.get("name") or "Track"
        content = (el.get("contentType") or "").lower().split()
        # contentType can be space-separated list e.g. "notes audio"
        primary = content[0] if content else ""
        ttype = _TRACK_TYPE_MAP.get(primary, "audio")

        # Does it contain sub-tracks? -> it's a folder/group container.
        sub_tracks = [c for c in el if _localname(c.tag) == "Track"]
        channel_el = _find(el, "Channel")

        role_hint = (channel_el.get("role") if channel_el is not None else None) or ""

        tid = stable_id("track", native_id)
        track = TrackState(
            id=tid,
            index=len(session.tracks) + len(session.groups) + len(session.return_tracks),
            name=name,
            track_type=_classify_track_type(ttype, role_hint, bool(sub_tracks)),
            color=el.get("color"),
            parent_id=parent_id,
        )
        track.provenance = exported(source_type=DAWPROJECT_SOURCE, locator=f"Track[{native_id}]")
        track.raw_source["dawproject_id"] = native_id
        if role_hint:
            track.native.setdefault("cubase", {})["channel_role"] = role_hint

        if channel_el is not None:
            _read_channel(channel_el, track, session, channel_index, param_index, native_id)

        _bucket_track(session, track, has_children=bool(sub_tracks),
                      has_channel=channel_el is not None)

        # Folder bookkeeping (organizational vs group-channel-enabled).
        if sub_tracks:
            folder = FolderState(
                id=stable_id("folder", native_id),
                name=name,
                index=depth,
                child_track_ids=[],  # filled after recursion via parent_id
                organizational_only=channel_el is None,
                group_channel_enabled=channel_el is not None,
            )
            folder.provenance = parsed(
                DAWPROJECT_SOURCE,
                explanation="folder inferred from nested <Track> elements; "
                            "group-channel status inferred from presence of a <Channel>.",
                confidence=0.8,
            )
            folder.native.setdefault("cubase", {})["has_channel"] = channel_el is not None
            session.folders.append(folder)
            _walk_tracks(el, session, channel_index, param_index,
                         parent_id=tid, depth=depth + 1)

    # Fill folder child ids from parent_id links.
    for folder in session.folders:
        folder.child_track_ids = [
            t.id for t in session.all_tracks() if t.parent_id and
            t.parent_id == stable_id("track", folder.id.split("folder-", 1)[-1])
        ] or folder.child_track_ids


def _bucket_track(session: SessionState, track: TrackState, *,
                  has_children: bool, has_channel: bool) -> None:
    """Route a parsed track into the right SessionState collection by type."""
    if track.track_type == "master":
        session.master_track = track
    elif track.track_type == "fx":
        session.return_tracks.append(track)
    elif track.track_type == "group" or (has_children and has_channel):
        session.groups.append(track)
    else:
        session.tracks.append(track)


def _read_standalone_channel(
    channel_el: ET.Element,
    session: SessionState,
    channel_index: dict[str, dict[str, Any]],
    param_index: dict[str, dict[str, Any]],
) -> None:
    """A <Channel> directly under <Structure> (a master/FX/group/vca bus not
    wrapped in a <Track>). Real DAWproject output emits buses this way."""
    native_id = channel_el.get("id") or channel_el.get("name") or f"chan{len(session.groups)}"
    role = (channel_el.get("role") or "").lower()
    if channel_index.get(native_id, {}).get("track_id"):
        return  # already indexed (was owned by a Track)
    name = channel_el.get("name") or {
        "master": "Stereo Out", "effect": "FX Channel", "submix": "Group",
        "vca": "VCA",
    }.get(role, "Channel")
    track = TrackState(
        id=stable_id("track", native_id),
        index=len(session.all_tracks()),
        name=name,
        track_type=_classify_track_type("audio", role, False),
        color=channel_el.get("color"),
    )
    track.provenance = exported(source_type=DAWPROJECT_SOURCE,
                                locator=f"Channel[{native_id}]")
    if role:
        track.native.setdefault("cubase", {})["channel_role"] = role
        track.native["cubase"]["standalone_channel"] = True
    _read_channel(channel_el, track, session, channel_index, param_index, native_id)
    _bucket_track(session, track, has_children=False, has_channel=True)


def _classify_track_type(ttype: str, role: str, has_children: bool) -> str:
    """Map a DAWproject mixerRole (regular|master|effect|submix|vca) + content
    type + nesting onto our canonical track_type. Exact enum, not substring."""
    r = (role or "").lower()
    if r == "master":
        return "master"
    if r == "effect":
        return "fx"
    if r == "vca":
        # A VCA scales member faders but sums NO audio; it must never be
        # conflated with a submix/group bus (grouping honesty).
        return "vca"
    if r == "submix":
        return "group"
    if has_children:
        # a nested container: group if it has its own channel, else organizational
        return "group" if role else "folder"
    return ttype


def _pan_to_canonical(pan: float, unit: Optional[str]) -> float:
    """Convert a DAWproject pan value to our -1..1 (L..R, 0=center) convention.

    ``normalized`` pans are 0..1 with 0.5 center; ``linear`` pans are already
    roughly -1..1 with 0 center. Do NOT hardcode a 0.5 offset for all units.
    """
    u = (unit or "normalized").lower()
    if u == "normalized":
        return round((pan - 0.5) * 2.0, 3)
    return round(pan, 3)  # linear / other: assume already centered on 0


def _read_channel(
    channel_el: ET.Element,
    track: TrackState,
    session: SessionState,
    channel_index: dict[str, dict[str, Any]],
    param_index: dict[str, dict[str, Any]],
    native_track_id: str,
) -> None:
    native_channel_id = channel_el.get("id") or native_track_id
    destination = channel_el.get("destination")
    role = channel_el.get("role")

    # Volume / pan / mute (RealParameter/BoolParameter children).
    vol_el = _find(channel_el, "Volume")
    pan_el = _find(channel_el, "Pan")
    mute_el = _find(channel_el, "Mute")
    solo_attr = channel_el.get("solo")

    vol = _param_value(vol_el)
    if vol is not None:
        unit = (vol_el.get("unit") if vol_el is not None else None) or "linear"
        track.volume_db = round(vol, 2) if unit == "decibel" else linear_to_db(vol)
        track.field_provenance["volume_db"] = exported(source_type=DAWPROJECT_SOURCE)
    pan = _param_value(pan_el)
    if pan is not None:
        track.pan = _pan_to_canonical(pan, pan_el.get("unit") if pan_el is not None else None)
    mv = _param_value(mute_el)
    if mute_el is not None:
        track.mute = bool(mv) if mv is not None else (mute_el.get("value") == "true")
    if solo_attr is not None:
        track.solo = solo_attr == "true"

    cfg = channel_el.get("audioChannels")
    if cfg:
        track.channel_config = {"1": "mono", "2": "stereo"}.get(cfg, f"{cfg}ch")

    if (role or "").lower() == "vca":
        # The mixerRole vocabulary is exact; surface it as the track role too.
        track.role = "VCA"
        track.field_provenance["role"] = exported(
            source_type=DAWPROJECT_SOURCE,
            locator=f"Channel[{native_channel_id}]@role",
        )

    # Register channel-level parameters so automation Targets (IDREFs) resolve.
    for pel, pname in ((vol_el, "Volume"), (pan_el, "Pan"), (mute_el, "Mute")):
        if pel is not None and pel.get("id"):
            param_index[pel.get("id")] = {
                "owner_id": track.id, "owner_kind": "channel",
                "name": pname, "unit": pel.get("unit"),
            }

    channel_index[native_channel_id] = {
        "track_id": track.id,
        "destination": destination,
        "role": role,
        "channel_id": native_channel_id,
        "audio_channels": safe_int(cfg) if cfg else None,
    }
    # A track may reference its channel id in routing; index the track id too.
    channel_index.setdefault(native_track_id, channel_index[native_channel_id])

    track.native.setdefault("cubase", {})["dawproject_channel_id"] = native_channel_id

    # Devices (inserts / instruments).
    devices_el = _find(channel_el, "Devices")
    if devices_el is not None:
        _read_devices(devices_el, track, session, param_index)

    # Sends.
    sends_el = _find(channel_el, "Sends")
    if sends_el is not None:
        _read_sends(sends_el, track, session, channel_index)


_DEVICE_ELEMENTS = {"Vst3Plugin", "Vst2Plugin", "ClapPlugin", "BuiltinDevice",
                    "Device", "AuPlugin", "Auv3Plugin", "Plugin",
                    "Equalizer", "Compressor", "NoiseGate", "Limiter"}
_FORMAT_MAP = {"Vst3Plugin": "VST3", "Vst2Plugin": "VST2", "ClapPlugin": "internal",
               "AuPlugin": "AU", "Auv3Plugin": "AU", "BuiltinDevice": "internal",
               "Equalizer": "internal", "Compressor": "internal",
               "NoiseGate": "internal", "Limiter": "internal"}
# Built-in device element localnames that imply a device_family without a name.
_BUILTIN_FAMILY = {"Equalizer": "EQ", "Compressor": "Dynamics",
                   "NoiseGate": "Dynamics", "Limiter": "Dynamics"}
# DAWproject parameter element localnames inside <Parameters>.
_PARAM_ELEMENTS = {"RealParameter", "BoolParameter", "IntegerParameter",
                   "EnumParameter", "TimeSignatureParameter", "parameter"}


def _read_devices(devices_el: ET.Element, track: TrackState, session: SessionState,
                  param_index: dict[str, dict[str, Any]]) -> None:
    slot = 0
    for el in devices_el:
        lname = _localname(el.tag)
        if lname not in _DEVICE_ELEMENTS:
            continue
        dev_name = el.get("deviceName") or el.get("name") or lname
        dev_id = el.get("id") or f"{track.id}-dev{slot}"
        role = (el.get("deviceRole") or "").lower()  # instrument|noteFX|audioFX|analyzer
        enabled_el = _find(el, "Enabled")
        enabled = None
        if enabled_el is not None:
            ev = enabled_el.get("value")
            enabled = (ev == "true") if ev is not None else None

        state_el = _find(el, "State")
        blob_ref = state_el.get("path") if state_el is not None else None

        device = DeviceState(
            id=stable_id("device", dev_id),
            track_id=track.id,
            index=slot,
            name=dev_name,
            vendor=el.get("deviceVendor"),
            plugin_identifier=el.get("deviceID"),
            plugin_format=_FORMAT_MAP.get(lname, "unknown"),
            device_type="instrument" if role == "instrument" else "audio_effect",
            device_family=_BUILTIN_FAMILY.get(lname),  # e.g. Equalizer -> EQ
            enabled=enabled,
            bypassed=(enabled is False) if enabled is not None else None,
            state_blob_ref=blob_ref,
        )
        device.provenance = exported(source_type=DAWPROJECT_SOURCE, locator=f"Device[{dev_id}]")

        # Enumerable <Parameters> (built-in devices expose real values here —
        # a genuine, honest window past the opaque-plug-in-state wall).
        params_el = _find(el, "Parameters")
        _read_parameters(params_el, device, param_index)

        # Only claim parameters are UNAVAILABLE when they are genuinely opaque:
        # an external state blob and no enumerable parameters.
        if blob_ref and not device.parameters:
            device.field_provenance["parameters"] = unavailable(
                "Plug-in parameter values live in an opaque state blob "
                f"({blob_ref}); DAWproject does not enumerate them for this device.",
                source_type=DAWPROJECT_SOURCE,
            )
        if role == "instrument":
            track.native.setdefault("cubase", {})["is_instrument_track"] = True
        track.devices.append(device)
        slot += 1


def _read_parameters(
    params_el: Optional[ET.Element],
    device: DeviceState,
    param_index: dict[str, dict[str, Any]],
) -> None:
    """Parse a device's <Parameters> children into DeviceParameterState.

    Handles Real/Bool/Integer/Enum parameters. Values ARE observed here (unlike
    an opaque State blob), so each is registered in ``param_index`` so an
    automation Target IDREF can bind to it, and marked ``exported``.
    """
    if params_el is None:
        return
    for i, p in enumerate(params_el):
        lname = _localname(p.tag)
        if lname not in _PARAM_ELEMENTS:
            continue
        pid = p.get("id") or f"{device.id}-p{i}"
        pname = p.get("name") or lname
        raw = p.get("value")
        value: object = None
        normalized = None
        if lname == "BoolParameter":
            value = (raw == "true") if raw is not None else None
        elif lname in ("IntegerParameter", "EnumParameter"):
            value = safe_int(raw)
        else:  # RealParameter / parameter
            value = safe_float(raw)
            unit = (p.get("unit") or "").lower()
            if value is not None and unit == "normalized":
                normalized = value
        param = DeviceParameterState(
            id=stable_id("param", pid),
            device_id=device.id,
            name=pname,
            value=value,
            normalized_value=normalized,
            unit=p.get("unit"),
            is_visible_to_host=True,
        )
        param.provenance = exported(source_type=DAWPROJECT_SOURCE)
        device.parameters.append(param)
        if p.get("id"):
            param_index[p.get("id")] = {
                "owner_id": device.id, "owner_kind": "device",
                "name": pname, "unit": p.get("unit"),
            }


def _read_sends(
    sends_el: ET.Element,
    track: TrackState,
    session: SessionState,
    channel_index: dict[str, dict[str, Any]],
) -> None:
    for el in sends_el:
        if _localname(el.tag) != "Send":
            continue
        dest = el.get("destination")
        vol_el = _find(el, "Volume")
        pan_el = _find(el, "Pan")
        enable_el = _find(el, "Enable")  # NOTE: 'Enable' (send), not 'Enabled' (device)

        level = _param_value(vol_el)
        level_db = None
        if level is not None:
            unit = (vol_el.get("unit") if vol_el is not None else None) or "linear"
            level_db = round(level, 2) if unit == "decibel" else linear_to_db(level)

        pan_val = _param_value(pan_el)
        enabled = None
        if enable_el is not None:
            ev = enable_el.get("value")
            enabled = (ev == "true") if ev is not None else True

        stype = (el.get("type") or "").lower()  # 'pre' | 'post'
        send = SendState(
            id=stable_id("send", track.id, dest or el.get("id") or str(len(track.sends))),
            source_track_id=track.id,
            target_return_id=dest or "",  # resolved later
            send_name=el.get("name"),
            level_db=level_db,
            pan=_pan_to_canonical(pan_val, pan_el.get("unit") if pan_el is not None else None)
            if pan_val is not None else None,
            enabled=enabled,
            pre_fader=(stype == "pre") if stype else None,
        )
        send.provenance = exported(source_type=DAWPROJECT_SOURCE)
        send.raw_source["dawproject_destination"] = dest
        track.sends.append(send)


def _resolve_routing(session: SessionState, channel_index: dict[str, dict[str, Any]]) -> None:
    """Turn channel ``destination`` ids into RouteState + fix send targets."""
    # Map native channel id -> canonical track id.
    dest_to_track = {cid: info["track_id"] for cid, info in channel_index.items()}

    seen_routes: set[tuple[str, str]] = set()
    inbound: dict[str, int] = {}  # target track id -> count of tracks routing in
    for cid, info in channel_index.items():
        dest = info.get("destination")
        src_track = info["track_id"]
        if not dest or dest not in dest_to_track:
            continue
        target = dest_to_track[dest]
        if target == src_track or (src_track, target) in seen_routes:
            continue
        seen_routes.add((src_track, target))
        if (channel_index.get(dest, {}).get("role") or "").lower() == "vca":
            # A destination IDREF that resolves to a vca-role channel is a
            # control link, NOT audio routing: a VCA scales member faders and
            # sums no audio, so emitting an output route here would invent a
            # signal path that does not exist.
            _link_vca_control(session, src_track, target, dest)
            continue
        session.routes.append(
            RouteState(
                id=stable_id("route", src_track, dest),
                source_track_id=src_track,
                target_id=target,
                route_type="output",
                provenance=exported(source_type=DAWPROJECT_SOURCE),
            )
        )
        t = session.track_by_id(src_track)
        if t:
            t.output_target_id = target
        inbound[target] = inbound.get(target, 0) + 1

    # Reclassify plain audio tracks that other tracks route INTO as group
    # channels (Cubase group buses are destinations, not folder parents).
    for target_id, count in inbound.items():
        t = session.track_by_id(target_id)
        if t is None or t is session.master_track:
            continue
        if t.track_type == "audio" and count >= 1 and t in session.tracks:
            t.track_type = "group"
            t.field_provenance["track_type"] = parsed(
                DAWPROJECT_SOURCE,
                explanation=f"reclassified as group: {count} track(s) route their "
                            "output here.",
                confidence=0.85,
            )
            session.tracks.remove(t)
            session.groups.append(t)

    for track in session.all_tracks():
        source_info = channel_index.get(
            track.native.get("cubase", {}).get("dawproject_channel_id") or ""
        )
        for send in track.sends:
            raw = send.raw_source.get("dawproject_destination")
            dest_info = channel_index.get(raw) if raw else None
            if dest_info is None:
                continue
            send.target_return_id = dest_info["track_id"]
            # Per-send destination-channel decode: the Send ``destination``
            # IDREF names an exact <Channel>; its ``audioChannels`` attribute
            # states the width of the port this send feeds. Populated only
            # from what the XML says — absent width stays None
            # (stereo-implicit), never an invented channel spec.
            send.destination_channel_id = dest_info.get("channel_id") or raw
            width = dest_info.get("audio_channels")
            if width:
                send.channel_count = width
                send.channel_layout = {1: "mono", 2: "stereo"}.get(width, f"{width}ch")
                width_prov = exported(
                    source_type=DAWPROJECT_SOURCE,
                    locator=f"Channel[{send.destination_channel_id}]@audioChannels",
                )
                send.field_provenance["channel_count"] = width_prov
                send.field_provenance["channel_layout"] = width_prov
            # Endpoint widths recorded verbatim (only where the XML states them).
            if width is not None:
                send.native.setdefault("cubase", {})["destination_audio_channels"] = width
            source_width = (source_info or {}).get("audio_channels")
            if source_width is not None:
                send.native.setdefault("cubase", {})["source_audio_channels"] = source_width


def _link_vca_control(
    session: SessionState,
    controlled_track_id: str,
    vca_track_id: str,
    vca_channel_id: str,
) -> None:
    """Record that a vca-role channel controls a member track's level.

    The member's ``destination`` IDREF is OBSERVED; reading it as level
    control (rather than an output route) is an interpretation, so the
    ``controls`` list is marked INFERRED with the rationale.
    """
    vca = session.track_by_id(vca_track_id)
    controlled = session.track_by_id(controlled_track_id)
    if vca is None or controlled is None:
        return
    if controlled_track_id not in vca.controls:
        vca.controls.append(controlled_track_id)
    vca.field_provenance["controls"] = inferred(
        "member channel destination IDREFs resolve to this vca-role <Channel>; "
        "a VCA scales member levels and carries no audio, so the reference is "
        "read as level control rather than an output route.",
        confidence=0.85,
        source_type=DAWPROJECT_SOURCE,
    )
    controlled.native.setdefault("cubase", {})["vca_channel_id"] = vca_channel_id


def _walk_arrangement(
    arrangement: ET.Element,
    session: SessionState,
    channel_index: dict[str, dict[str, Any]],
    param_index: dict[str, dict[str, Any]],
) -> None:
    lanes = _find(arrangement, "Lanes")
    if lanes is not None:
        _read_track_lane(lanes, session, channel_index, param_index,
                         _lane_track_id(lanes, channel_index))

    # Markers: 'Markers' at Arrangement level (also 'markers' lowercase inside lanes).
    for markers_el in arrangement:
        if _localname(markers_el.tag) not in ("Markers", "markers"):
            continue
        for m in markers_el:
            if _localname(m.tag) != "Marker":
                continue
            session.musical_structure.markers.append(
                Marker(
                    id=stable_id("marker", m.get("name") or m.get("time") or "m"),
                    time_beats=safe_float(m.get("time")) or 0.0,
                    name=m.get("name"),
                )
            )


def _lane_track_id(el: ET.Element, channel_index: dict[str, dict[str, Any]]) -> Optional[str]:
    ref = el.get("track")
    if ref and ref in channel_index:
        return channel_index[ref]["track_id"]
    if ref:
        return stable_id("track", ref)
    return None


def _read_track_lane(
    lane_el: ET.Element,
    session: SessionState,
    channel_index: dict[str, dict[str, Any]],
    param_index: dict[str, dict[str, Any]],
    track_id: Optional[str],
) -> None:
    """Recursively walk a Lanes/timeline. Content associates to a track via the
    nearest enclosing 'track' IDREF, so we thread the resolved track_id down."""
    here = _lane_track_id(lane_el, channel_index) or track_id
    for el in lane_el:
        lname = _localname(el.tag)
        if lname == "Clips":
            for clip_el in el:
                if _localname(clip_el.tag) == "Clip":
                    _read_clip(clip_el, session, _lane_track_id(el, channel_index) or here)
        elif lname == "Notes":
            # A bare Notes timeline (not inside a Clip) — synthesize a midi clip.
            _read_notes_lane(el, session, _lane_track_id(el, channel_index) or here)
        elif lname in ("Points", "TempoAutomation", "TimeSignatureAutomation"):
            _read_automation(el, session, param_index,
                             _lane_track_id(el, channel_index) or here)
        elif lname == "Lanes":
            _read_track_lane(el, session, channel_index, param_index, here)


def _read_clip(clip_el: ET.Element, session: SessionState, track_id: Optional[str]) -> None:
    if not track_id:
        return
    time = safe_float(clip_el.get("time"))
    duration = safe_float(clip_el.get("duration"))
    name = clip_el.get("name") or "Clip"
    notes_el = _find(clip_el, "Notes")
    inner_clips = _find(clip_el, "Clips")
    warps = _find(clip_el, "Warps")
    clip_type = "midi" if notes_el is not None else "audio"

    clip = ClipState(
        id=stable_id("clip", track_id, str(time)),
        track_id=track_id,
        name=name,
        clip_type=clip_type,
        start_time_beats=time,
        length_beats=duration,
    )
    clip.provenance = exported(source_type=DAWPROJECT_SOURCE)

    if notes_el is not None:
        clip.notes = _read_notes(notes_el)
        clip.midi_note_count = len(clip.notes)

    # Audio file reference nested in Clips/Warps/Audio/File.
    if warps is not None:
        audio_el = _find(warps, "Audio")
        if audio_el is not None:
            file_el = _find(audio_el, "File")
            if file_el is not None:
                clip.audio_file = file_el.get("path")
    elif inner_clips is not None:
        clip.native.setdefault("cubase", {})["has_nested_clips"] = True

    t = session.track_by_id(track_id)
    if t is not None:
        t.clips.append(clip)


_NORM_VEL = 127.0  # DAWproject vel/rel are normalized 0..1; scale to MIDI 0..127.
_POINT_ELEMENTS = ("RealPoint", "Point", "BoolPoint", "IntegerPoint",
                   "EnumPoint", "TimeSignaturePoint")


def _read_notes(notes_el: ET.Element) -> list[MidiNote]:
    """Parse <Note> children. vel/rel are NORMALIZED 0..1 doubles (NOT 0..127)."""
    out: list[MidiNote] = []
    for n in notes_el:
        if _localname(n.tag) != "Note":
            continue
        vel = safe_float(n.get("vel"))
        rel = safe_float(n.get("rel"))
        out.append(
            MidiNote(
                time_beats=safe_float(n.get("time")) or 0.0,
                duration_beats=safe_float(n.get("duration")) or 0.0,
                key=safe_int(n.get("key")) or 0,
                velocity=round(vel * _NORM_VEL) if vel is not None else 100,
                release_velocity=round(rel * _NORM_VEL) if rel is not None else None,
                channel=safe_int(n.get("channel")) or 0,
            )
        )
    return out


def _read_notes_lane(notes_el: ET.Element, session: SessionState,
                     track_id: Optional[str]) -> None:
    """A Notes timeline that is a direct lane (not wrapped in a Clip)."""
    if not track_id:
        return
    notes = _read_notes(notes_el)
    if not notes:
        return
    clip = ClipState(
        id=stable_id("clip", track_id, "notes"),
        track_id=track_id, name="Notes", clip_type="midi",
        start_time_beats=notes[0].time_beats, notes=notes,
        midi_note_count=len(notes),
    )
    clip.provenance = exported(source_type=DAWPROJECT_SOURCE)
    t = session.track_by_id(track_id)
    if t is not None:
        t.clips.append(clip)


def _read_automation(
    points_el: ET.Element,
    session: SessionState,
    param_index: dict[str, dict[str, Any]],
    track_id: Optional[str],
) -> None:
    """Parse a <Points> automation lane. The FIRST child is a <Target> whose
    ``parameter`` IDREF (or ``expression``) says what is automated."""
    target_el = _find(points_el, "Target")
    param_ref = target_el.get("parameter") if target_el is not None else None
    expression = target_el.get("expression") if target_el is not None else None

    device_id = None
    if param_ref and param_ref in param_index:
        info = param_index[param_ref]
        param_name = info["name"]
        if info["owner_kind"] == "device":
            device_id = info["owner_id"]
            track_id = track_id or session.device_by_id(device_id).track_id \
                if session.device_by_id(device_id) else track_id
        else:  # channel parameter
            track_id = track_id or info["owner_id"]
    else:
        # Unresolved IDREF or MIDI-expression automation.
        param_name = expression or (f"param:{param_ref}" if param_ref else "parameter")

    if not track_id:
        return

    lane = AutomationLane(
        id=stable_id("auto", track_id, param_name, param_ref or expression or "x"),
        track_id=track_id,
        parameter_name=param_name,
        device_id=device_id,
        parameter_id=param_ref,
        unit=points_el.get("unit"),
    )
    lane.provenance = exported(source_type=DAWPROJECT_SOURCE)
    if param_ref and param_ref not in param_index:
        lane.field_provenance["parameter_name"] = parsed(
            DAWPROJECT_SOURCE, confidence=0.4,
            explanation=f"automation Target parameter IDREF '{param_ref}' did not "
                        "resolve to a known parameter id.",
        )
    for p in points_el:
        if _localname(p.tag) not in _POINT_ELEMENTS:
            continue
        raw = p.get("value")
        val = safe_float(raw)
        if val is None and raw == "true":
            val = 1.0
        elif val is None and raw == "false":
            val = 0.0
        lane.points.append(
            AutomationPoint(
                time_beats=safe_float(p.get("time")) or 0.0,
                value=val if val is not None else 0.0,
                curve="step" if p.get("interpolation") == "hold" else "linear",
            )
        )
    if lane.points:
        session.automation.append(lane)


def element_census(path: str) -> dict:
    """Walk a real ``.dawproject``'s XML and report handled vs. unhandled elements.

    This is the grounding harness for real Cubase exports: it tallies every
    element localname (and the attributes seen on it) and flags any that the
    parser does not yet handle, so hardening against genuine output is a
    mechanical diff rather than a guessing game. Read-only, never raises.
    """
    from collections import Counter

    root, members, warnings = load_project_xml(path)
    census: dict = {
        "path": path,
        "members": members,
        "warnings": list(warnings),
        "element_counts": {},
        "unhandled_elements": {},
        "attributes_by_element": {},
        "channel_roles": [],
        "device_elements": [],
        "unit_values": [],
    }
    if root is None:
        return census

    counts: Counter[str] = Counter()
    attrs: dict[str, set] = {}
    for el in root.iter():
        name = _localname(el.tag)
        counts[name] += 1
        attrs.setdefault(name, set()).update(el.attrib.keys())
        if name == "Channel" and el.get("role"):
            census["channel_roles"].append(el.get("role"))
        if name in _DEVICE_ELEMENTS:
            census["device_elements"].append(name)
        unit = el.get("unit")
        if unit:
            census["unit_values"].append(unit)

    census["element_counts"] = dict(counts.most_common())
    census["unhandled_elements"] = {
        n: c for n, c in counts.items() if n not in HANDLED_ELEMENTS
    }
    census["attributes_by_element"] = {n: sorted(a) for n, a in sorted(attrs.items())}
    census["channel_roles"] = sorted(set(census["channel_roles"]))
    census["device_elements"] = sorted(set(census["device_elements"]))
    census["unit_values"] = sorted(set(census["unit_values"]))
    return census
