"""Session-bundle discovery & fingerprinting.

A CubaseSessionBundle is a directory holding whatever artifacts exist for a
project; not every bundle has every artifact. A bare ``.cpr`` (or ``.dawproject``)
path is also accepted and treated as a one-artifact bundle. Discovery classifies
each file by extension/content into an artifact type the extractors understand.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .utils import sha256_file

# extension -> artifact type
_EXT_MAP = {
    ".dawproject": "dawproject",
    ".cpr": "cpr",
    ".npr": "cpr",
    ".mid": "midi_export",
    ".midi": "midi_export",
    ".musicxml": "musicxml_export",
    ".mxl": "musicxml_export",
    ".wav": "rendered_audio",
    ".aiff": "rendered_audio",
    ".aif": "rendered_audio",
    ".flac": "rendered_audio",
    ".vstpreset": "preset",
    ".trackpreset": "preset",
    ".fxbpreset": "preset",
    ".xml": "track_archive",       # refined by content sniff below
    ".json": "midi_remote",        # runtime snapshot (refined below)
}


@dataclass
class Artifact:
    path: str
    artifact_type: str
    size: int
    sha256: str = ""


@dataclass
class Bundle:
    root: str
    artifacts: list[Artifact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def of_type(self, t: str) -> list[Artifact]:
        return [a for a in self.artifacts if a.artifact_type == t]

    def types(self) -> list[str]:
        return sorted({a.artifact_type for a in self.artifacts})


def _sniff_xml(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096).lower()
    except OSError:
        return "track_archive"
    if b"dawproject" in head or b"<project" in head:
        return "dawproject"
    if b"trackarchive" in head or b"class=" in head or b"maudiotrack" in head:
        return "track_archive"
    if b"score-partwise" in head or b"musicxml" in head:
        return "musicxml_export"
    return "track_archive"


def _sniff_json(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            head = fh.read(2048).lower()
    except OSError:
        return "midi_remote"
    if b"channels" in head or b"transport" in head or b"midi_remote" in head:
        return "midi_remote"
    return "midi_remote"


def classify(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    t = _EXT_MAP.get(ext)
    if t is None:
        return None
    if ext == ".xml":
        return _sniff_xml(path)
    if ext == ".json":
        return _sniff_json(path)
    return t


def discover(path: str, hash_files: bool = True) -> Bundle:
    """Discover artifacts in a bundle dir, or wrap a single file."""
    if os.path.isfile(path):
        root = os.path.dirname(path) or "."
        bundle = Bundle(root=root)
        t = classify(path)
        if t is None:
            bundle.warnings.append(f"Unrecognized artifact type for {path}")
        else:
            bundle.artifacts.append(_make_artifact(path, t, hash_files))
        return bundle

    bundle = Bundle(root=path)
    if not os.path.isdir(path):
        bundle.warnings.append(f"Path does not exist: {path}")
        return bundle

    for dirpath, _dirs, files in os.walk(path):
        for name in sorted(files):
            fpath = os.path.join(dirpath, name)
            t = classify(fpath)
            if t is not None:
                bundle.artifacts.append(_make_artifact(fpath, t, hash_files))
    if not bundle.artifacts:
        bundle.warnings.append("No recognizable Cubase artifacts found in bundle.")
    return bundle


def _make_artifact(path: str, t: str, hash_files: bool) -> Artifact:
    size = os.path.getsize(path) if os.path.exists(path) else 0
    digest = ""
    if hash_files and size:
        try:
            digest = sha256_file(path)
        except OSError:
            pass
    return Artifact(path=path, artifact_type=t, size=size, sha256=digest)
