"""VST3 preset (.vstpreset) extractor — the "preset lab".

Grounded in the official VST3 SDK's documented preset file format
(``public.sdk/source/vst/vstpresetfile.h`` — the layout is documented in the
header comment; only the factual format constants are used here, no SDK code):

    0   +---------------------------+
        | 'VST3'                    |  4 bytes  (header id)
        | version                   |  4 bytes  (int32 LE, currently 1)
        | ASCII-encoded class id    | 32 bytes  (component/processor FUID)
     +--| offset to chunk list      |  8 bytes  (int64 LE)
     |  +---------------------------+
     |  | DATA AREA (chunk data)    |
     +->+---------------------------+
        | 'List'                    |  4 bytes
        | entry count               |  4 bytes  (int32 LE)
        |  n x [ chunk id (4)       |
        |        offset   (int64)   |
        |        size     (int64) ] |
        +---------------------------+

Chunk ids: ``Comp`` (component state), ``Cont`` (controller state),
``Prog`` (program data), ``Info`` (meta-info XML).

What this extractor honestly yields:

* **plug-in identity** — the 32-char class id, and, when an ``Info`` chunk is
  present, MediaBay attributes (``PlugInName``, ``PlugInCategory``, ``Name``)
  — HIGH confidence, parsed.
* **state fingerprints** — the ``Comp``/``Cont`` chunks are the plug-in's own
  opaque serialization; parameter VALUES are NOT decodable generically. We
  fingerprint the bytes so a controlled change is *detectable* (and diffable)
  without fabricating a value. Same posture as the DAWproject State blob.

Read-only; never raises on malformed input.
"""

from __future__ import annotations

import re
import struct
import sys
from dataclasses import dataclass, field
from typing import Optional
from xml.etree import ElementTree as ET

from ..utils import sha256_bytes

VSTPRESET_SOURCE = "preset"

_HEADER = struct.Struct("<4si32sq")   # magic, version, classid, list offset
_ENTRY = struct.Struct("<4sqq")       # chunk id, offset, size
_MAGIC = b"VST3"
_LIST = b"List"

# MediaBay attribute keys from pluginterfaces/vst/vstpresetkeys.h
_KNOWN_KEYS = ("PlugInName", "PlugInCategory", "Name", "FileName",
               "MusicalInstrument", "MusicalStyle", "MusicalCharacter",
               "StateType", "FilePathString")


@dataclass
class PresetChunk:
    chunk_id: str
    offset: int
    size: int
    sha16: str = ""      # first 16 hex chars of sha256 of the chunk bytes


@dataclass
class VstPresetResult:
    ok: bool = False
    path: str = ""
    version: Optional[int] = None
    class_id: Optional[str] = None        # 32-char ASCII FUID (processor part)
    chunks: list[PresetChunk] = field(default_factory=list)
    plugin_name: Optional[str] = None     # from Info chunk, when present
    plugin_category: Optional[str] = None
    preset_name: Optional[str] = None
    attributes: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def chunk(self, chunk_id: str) -> Optional[PresetChunk]:
        for c in self.chunks:
            if c.chunk_id == chunk_id:
                return c
        return None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "path": self.path, "version": self.version,
            "class_id": self.class_id,
            "chunks": [vars(c) for c in self.chunks],
            "plugin_name": self.plugin_name,
            "plugin_category": self.plugin_category,
            "preset_name": self.preset_name,
            "attributes": self.attributes,
            "warnings": self.warnings,
        }


def extract(path: str, max_bytes: int = 256 * 1024 * 1024) -> VstPresetResult:
    result = VstPresetResult(path=path)
    try:
        with open(path, "rb") as fh:
            data = fh.read(max_bytes)
    except OSError as exc:
        result.warnings.append(f"Cannot read preset: {exc}")
        return result

    if len(data) < _HEADER.size or data[:4] != _MAGIC:
        result.warnings.append("Not a VST3 preset (missing 'VST3' header id).")
        return result

    magic, version, classid_raw, list_offset = _HEADER.unpack_from(data, 0)
    result.version = version
    classid = classid_raw.decode("ascii", "replace").strip("\x00")
    if re.fullmatch(r"[0-9A-Fa-f]{32}", classid):
        result.class_id = classid.upper()
    else:
        result.class_id = classid or None
        result.warnings.append("Class id is not 32 hex chars; kept verbatim.")

    if not (0 < list_offset <= len(data) - 8):
        result.warnings.append(
            f"Chunk-list offset {list_offset} out of bounds (file {len(data)}B); "
            "cannot read chunk list.")
        return result

    if data[list_offset:list_offset + 4] != _LIST:
        result.warnings.append("No 'List' marker at chunk-list offset.")
        return result
    (entry_count,) = struct.unpack_from("<i", data, list_offset + 4)
    if not (0 <= entry_count <= 128):
        result.warnings.append(f"Implausible chunk-entry count {entry_count}.")
        return result

    pos = list_offset + 8
    for _ in range(entry_count):
        if pos + _ENTRY.size > len(data):
            result.warnings.append("Chunk list truncated.")
            break
        cid, off, size = _ENTRY.unpack_from(data, pos)
        pos += _ENTRY.size
        chunk = PresetChunk(chunk_id=cid.decode("latin-1"), offset=off, size=size)
        if 0 <= off and off + size <= len(data) and size >= 0:
            chunk.sha16 = sha256_bytes(data[off:off + size])[:16]
        else:
            result.warnings.append(f"Chunk {chunk.chunk_id} bounds invalid "
                                   f"(off={off}, size={size}).")
        result.chunks.append(chunk)

    info = result.chunk("Info")
    if info and info.sha16:
        _parse_meta_info(data[info.offset:info.offset + info.size], result)

    result.ok = True
    return result


def _parse_meta_info(blob: bytes, result: VstPresetResult) -> None:
    """Parse the Info chunk (MediaBay-style XML). Tolerant: XML first, then a
    regex scan for known keys — never raises."""
    text = blob.decode("utf-8", "replace")
    try:
        root = ET.fromstring(text.strip("\x00").strip())
        for el in root.iter():
            key = el.get("id") or el.get("name")
            value = el.get("value")
            if key and value is not None:
                result.attributes[key] = value
    except ET.ParseError:
        # fall back: attribute-pair scan for the documented keys
        for key in _KNOWN_KEYS:
            m = re.search(rf'{key}"\s+value="([^"]*)"', text) or \
                re.search(rf"{key}=['\"]([^'\"]*)['\"]", text)
            if m:
                result.attributes[key] = m.group(1)
        if not result.attributes:
            result.warnings.append("Info chunk present but not parseable as XML.")

    result.plugin_name = result.attributes.get("PlugInName")
    result.plugin_category = result.attributes.get("PlugInCategory")
    result.preset_name = result.attributes.get("Name")


def diff(path_a: str, path_b: str) -> dict:
    """Compare two presets. A controlled single-parameter change shows up as a
    Comp-chunk fingerprint/size delta — detectable, honestly, without claiming
    to know WHICH parameter moved."""
    a, b = extract(path_a), extract(path_b)
    out: dict = {
        "a": path_a, "b": path_b,
        "same_plugin": bool(a.class_id and a.class_id == b.class_id),
        "class_id_a": a.class_id, "class_id_b": b.class_id,
        "chunk_deltas": [],
        "note": "Chunk fingerprints attribute a controlled edit to the plug-in's "
                "opaque state; parameter values are not decodable generically.",
    }
    ids = {c.chunk_id for c in a.chunks} | {c.chunk_id for c in b.chunks}
    for cid in sorted(ids):
        ca, cb = a.chunk(cid), b.chunk(cid)
        entry = {"chunk": cid,
                 "in_a": ca is not None, "in_b": cb is not None,
                 "size_a": ca.size if ca else None,
                 "size_b": cb.size if cb else None,
                 "changed": bool(ca and cb and ca.sha16 != cb.sha16)}
        out["chunk_deltas"].append(entry)
    out["state_changed"] = any(d["changed"] for d in out["chunk_deltas"]
                               if d["chunk"] in ("Comp", "Cont"))
    return out


# --- CLI (entry point: preset-lab) -----------------------------------------

def _cli(argv: Optional[list[str]] = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="preset-lab", description="VST3 preset inspector (read-only)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect", help="Parse one .vstpreset")
    p.add_argument("path")
    p = sub.add_parser("diff", help="Compare two controlled presets")
    p.add_argument("a")
    p.add_argument("b")
    args = parser.parse_args(argv)

    if args.cmd == "inspect":
        print(json.dumps(extract(args.path).to_dict(), indent=2))
    else:
        print(json.dumps(diff(args.a, args.b), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
