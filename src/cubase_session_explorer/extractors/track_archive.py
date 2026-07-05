"""Cubase Track Archive (.xml) extractor.

A Track Archive is Cubase's XML track export. Its elements are generic
(``obj``, ``list``, ``member``) with semantics in ``class`` attributes
(``MAudioTrackEvent``, ``MMidiPartEvent``, ``PluginRef`` ...). We do a cautious
structural pass: enough to recover track names and types and count events, but
we do NOT claim to reconstruct routing or parameters from it. Everything is
tagged ``parsed`` with sub-1.0 confidence and kept honest.
"""

from __future__ import annotations

import gzip
import io
from dataclasses import dataclass, field
from typing import Optional
from xml.etree import ElementTree as ET

from ..ids import stable_id
from ..models import ProjectMeta, SessionState, TrackState
from ..provenance import parsed

TRACK_ARCHIVE_SOURCE = "track_archive"

_TRACK_CLASS_MAP = {
    "MAudioTrackEvent": "audio",
    "MMidiTrackEvent": "midi",
    "MInstrumentTrackEvent": "instrument",
    "MGroupTrackEvent": "group",
    "MFxTrackEvent": "fx",
    "MFolderTrackEvent": "folder",
}


@dataclass
class TrackArchiveResult:
    ok: bool = False
    session: Optional[SessionState] = None
    warnings: list[str] = field(default_factory=list)


def _attr_name(obj: ET.Element) -> Optional[str]:
    """Track Archives store the display name in a <string name="Name" value="..."/>."""
    for m in obj.iter():
        if m.tag in ("string", "str") and m.get("name") in ("Name", "name"):
            return m.get("value")
    return None


def extract(path: str) -> TrackArchiveResult:
    result = TrackArchiveResult()
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        result.warnings.append(f"Cannot read track archive: {exc}")
        return result

    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError as exc:
            result.warnings.append(f"gzip decompress failed: {exc}")
            return result

    try:
        root = ET.parse(io.BytesIO(data)).getroot()
    except ET.ParseError as exc:
        result.warnings.append(f"XML parse failed: {exc}")
        return result

    artifact = path.rsplit("/", 1)[-1]
    session = SessionState(
        project=ProjectMeta(project_name=artifact.rsplit(".", 1)[0], project_path=path)
    )
    session.provenance = parsed(TRACK_ARCHIVE_SOURCE, confidence=0.7, artifact=artifact)
    session.capture.artifacts.append(TRACK_ARCHIVE_SOURCE)
    session.capture.extractors_run.append("track_archive")

    idx = 0
    for obj in root.iter():
        cls = obj.get("class")
        if cls in _TRACK_CLASS_MAP:
            name = _attr_name(obj) or f"{_TRACK_CLASS_MAP[cls].title()} {idx + 1}"
            track = TrackState(
                id=stable_id("track", f"ta-{idx}-{name}"),
                index=idx,
                name=name,
                track_type=_TRACK_CLASS_MAP[cls],
            )
            track.provenance = parsed(
                TRACK_ARCHIVE_SOURCE, confidence=0.75,
                explanation=f"track type from class attribute '{cls}'",
                locator=cls,
            )
            track.native.setdefault("cubase", {})["track_archive_class"] = cls
            bucket = {
                "fx": session.return_tracks,
                "group": session.groups,
            }.get(track.track_type, session.tracks)
            bucket.append(track)
            idx += 1

    if idx == 0:
        result.warnings.append(
            "No track-class objects found; file may not be a Cubase Track Archive."
        )
    result.session = session
    result.ok = idx > 0
    return result
