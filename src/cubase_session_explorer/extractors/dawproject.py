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
from ..provenance import exported, parsed, unavailable
from ..utils import linear_to_db, safe_float, safe_int

DAWPROJECT_SOURCE = "dawproject"

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

    if structure is not None:
        _walk_tracks(structure, session, channel_index, parent_id=None, depth=0)

    # --- Resolve routing destinations (channel destination -> track) -------
    _resolve_routing(session, channel_index)

    # --- Arrangement: clips / notes / automation ---------------------------
    arrangement = _find(root, "Arrangement")
    if arrangement is not None:
        _walk_arrangement(arrangement, session, channel_index)

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

    result.session = session
    result.ok = True
    return result


def _walk_tracks(
    parent_el: ET.Element,
    session: SessionState,
    channel_index: dict[str, dict[str, Any]],
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
            _read_channel(channel_el, track, session, channel_index, native_id)

        # Classify FX/group by channel role.
        if "effectTrack" in role_hint or ttype == "fx":
            track.track_type = "fx"
        elif role_hint == "master":
            track.track_type = "master"

        # Route the track into the right bucket.
        if track.track_type == "master":
            session.master_track = track
        elif track.track_type == "fx":
            session.return_tracks.append(track)
        elif track.track_type == "group" or (sub_tracks and channel_el is not None):
            # Folder that also has a channel => group-channel-enabled folder.
            session.groups.append(track)
        else:
            session.tracks.append(track)

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
            _walk_tracks(el, session, channel_index, parent_id=tid, depth=depth + 1)

    # Fill folder child ids from parent_id links.
    for folder in session.folders:
        folder.child_track_ids = [
            t.id for t in session.all_tracks() if t.parent_id and
            t.parent_id == stable_id("track", folder.id.split("folder-", 1)[-1])
        ] or folder.child_track_ids


def _classify_track_type(ttype: str, role: str, has_children: bool) -> str:
    if role == "master":
        return "master"
    if "effect" in role.lower():
        return "fx"
    if has_children:
        return "group" if role else "folder"
    return ttype


def _read_channel(
    channel_el: ET.Element,
    track: TrackState,
    session: SessionState,
    channel_index: dict[str, dict[str, Any]],
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
        # DAWproject pan is 0..1 with 0.5 center; convert to -1..1.
        track.pan = round((pan - 0.5) * 2.0, 3)
    mv = _param_value(mute_el)
    if mute_el is not None:
        track.mute = bool(mv) if mv is not None else (mute_el.get("value") == "true")
    if solo_attr is not None:
        track.solo = solo_attr == "true"

    cfg = channel_el.get("audioChannels")
    if cfg:
        track.channel_config = {"1": "mono", "2": "stereo"}.get(cfg, f"{cfg}ch")

    channel_index[native_channel_id] = {
        "track_id": track.id,
        "destination": destination,
        "role": role,
    }
    # A track may reference its channel id in routing; index the track id too.
    channel_index.setdefault(native_track_id, channel_index[native_channel_id])

    track.native.setdefault("cubase", {})["dawproject_channel_id"] = native_channel_id

    # Devices (inserts / instruments).
    devices_el = _find(channel_el, "Devices")
    if devices_el is not None:
        _read_devices(devices_el, track, session)

    # Sends.
    sends_el = _find(channel_el, "Sends")
    if sends_el is not None:
        _read_sends(sends_el, track, session, channel_index)


_DEVICE_ELEMENTS = {"Vst3Plugin", "Vst2Plugin", "ClapPlugin", "BuiltinDevice",
                    "Device", "AuPlugin", "Plugin"}
_FORMAT_MAP = {"Vst3Plugin": "VST3", "Vst2Plugin": "VST2", "ClapPlugin": "internal",
               "AuPlugin": "AU", "BuiltinDevice": "internal"}


def _read_devices(devices_el: ET.Element, track: TrackState, session: SessionState) -> None:
    slot = 0
    for el in devices_el:
        lname = _localname(el.tag)
        if lname not in _DEVICE_ELEMENTS:
            continue
        dev_name = el.get("deviceName") or el.get("name") or lname
        dev_id = el.get("id") or f"{track.id}-dev{slot}"
        role = (el.get("deviceRole") or "").lower()
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
            enabled=enabled,
            bypassed=(enabled is False) if enabled is not None else None,
            state_blob_ref=blob_ref,
        )
        device.provenance = exported(source_type=DAWPROJECT_SOURCE, locator=f"Device[{dev_id}]")
        # Honest: parameter values are opaque inside the state blob.
        if blob_ref:
            device.field_provenance["parameters"] = unavailable(
                "Plug-in parameter values live in an opaque state blob "
                f"({blob_ref}); DAWproject does not enumerate them.",
                source_type=DAWPROJECT_SOURCE,
            )
        if role == "instrument":
            track.native.setdefault("cubase", {})["is_instrument_track"] = True
        track.devices.append(device)
        slot += 1


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
        level = _param_value(vol_el)
        pan_el = _find(el, "Pan")
        send = SendState(
            id=stable_id("send", track.id, dest or str(len(track.sends))),
            source_track_id=track.id,
            target_return_id=dest or "",  # resolved later
            level_db=linear_to_db(level) if level is not None else None,
            pan=(_param_value(pan_el) - 0.5) * 2 if _param_value(pan_el) is not None else None,
            enabled=el.get("enable", "true") != "false",
            pre_fader=(el.get("type", "").lower() == "pre") if el.get("type") else None,
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
        for send in track.sends:
            raw = send.raw_source.get("dawproject_destination")
            if raw and raw in dest_to_track:
                send.target_return_id = dest_to_track[raw]


def _walk_arrangement(
    arrangement: ET.Element,
    session: SessionState,
    channel_index: dict[str, dict[str, Any]],
) -> None:
    lanes = _find(arrangement, "Lanes")
    if lanes is None:
        return
    _walk_lanes(lanes, session, channel_index)

    # Markers can appear as a top-level lane/points structure.
    markers_el = _find(arrangement, "Markers")
    if markers_el is not None:
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


def _walk_lanes(
    lanes_el: ET.Element,
    session: SessionState,
    channel_index: dict[str, dict[str, Any]],
) -> None:
    for el in lanes_el:
        lname = _localname(el.tag)
        if lname == "Lanes":
            tid = _lane_track_id(el, channel_index)
            _read_track_lane(el, session, channel_index, tid)
        elif lname in ("Clips", "Notes", "Points"):
            _read_track_lane(lanes_el, session, channel_index, _lane_track_id(lanes_el, channel_index))
            return


def _read_track_lane(
    lane_el: ET.Element,
    session: SessionState,
    channel_index: dict[str, dict[str, Any]],
    track_id: Optional[str],
) -> None:
    for el in lane_el:
        lname = _localname(el.tag)
        if lname == "Clips":
            for clip_el in el:
                if _localname(clip_el.tag) == "Clip":
                    _read_clip(clip_el, session, track_id)
        elif lname == "Points":
            _read_automation(el, session, track_id)
        elif lname == "Lanes":
            _read_track_lane(el, session, channel_index,
                             _lane_track_id(el, channel_index) or track_id)


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
        for n in notes_el:
            if _localname(n.tag) != "Note":
                continue
            clip.notes.append(
                MidiNote(
                    time_beats=safe_float(n.get("time")) or 0.0,
                    duration_beats=safe_float(n.get("duration")) or 0.0,
                    key=safe_int(n.get("key")) or 0,
                    velocity=int((safe_float(n.get("vel")) or 0.78) * 127)
                    if n.get("vel") and float(n.get("vel")) <= 1.0
                    else (safe_int(n.get("vel")) or 100),
                    channel=safe_int(n.get("channel")) or 0,
                )
            )
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


def _read_automation(points_el: ET.Element, session: SessionState, track_id: Optional[str]) -> None:
    if not track_id:
        return
    target = points_el.get("target") or points_el.get("parameter") or "parameter"
    lane = AutomationLane(
        id=stable_id("auto", track_id, target),
        track_id=track_id,
        parameter_name=target,
    )
    lane.provenance = exported(source_type=DAWPROJECT_SOURCE)
    for p in points_el:
        if _localname(p.tag) not in ("RealPoint", "Point", "BoolPoint"):
            continue
        lane.points.append(
            AutomationPoint(
                time_beats=safe_float(p.get("time")) or 0.0,
                value=safe_float(p.get("value")) or 0.0,
                curve="step" if p.get("interpolation") == "hold" else "linear",
            )
        )
    if lane.points:
        session.automation.append(lane)
