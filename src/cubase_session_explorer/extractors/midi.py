"""Standard MIDI File extractor (Cubase 'Export > MIDI File').

A dependency-free SMF (format 0/1) reader: track names, tempo, time signature,
and note events with onset/duration/velocity. Enough to ground the MIDI-content
layer of the session without requiring ``mido``. If ``mido`` is present the
caller may prefer it, but this keeps fixtures and tests self-contained.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

from ..models import MidiNote


@dataclass
class MidiTrackData:
    name: Optional[str] = None
    notes: list[MidiNote] = field(default_factory=list)
    channel: int = 0


@dataclass
class MidiResult:
    ok: bool = False
    division: int = 480          # ticks per quarter note
    tempo_bpm: Optional[float] = None
    time_signature: Optional[str] = None
    tracks: list[MidiTrackData] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _read_varlen(data: bytes, i: int) -> tuple[int, int]:
    value = 0
    while True:
        byte = data[i]
        i += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            break
    return value, i


def extract(path: str) -> MidiResult:
    result = MidiResult()
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        result.warnings.append(f"Cannot read MIDI file: {exc}")
        return result

    if data[:4] != b"MThd":
        result.warnings.append("Not a Standard MIDI File (missing MThd).")
        return result

    _, ntrks, division = struct.unpack(">HHH", data[8:14])
    result.division = division or 480
    pos = 14

    for _ in range(ntrks):
        if data[pos:pos + 4] != b"MTrk":
            break
        length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        pos += 8
        end = pos + length
        track = MidiTrackData()
        abs_ticks = 0
        active: dict[tuple[int, int], tuple[int, int]] = {}  # (chan,key)->(start,vel)
        status = 0
        i = pos
        while i < end:
            delta, i = _read_varlen(data, i)
            abs_ticks += delta
            byte = data[i]
            if byte & 0x80:
                status = byte
                i += 1
            # running status reuses previous status
            event = status & 0xF0
            channel = status & 0x0F
            if status == 0xFF:  # meta
                meta_type = data[i]; i += 1
                mlen, i = _read_varlen(data, i)
                payload = data[i:i + mlen]; i += mlen
                if meta_type == 0x03 and track.name is None:
                    track.name = payload.decode("latin-1", "replace")
                elif meta_type == 0x51 and result.tempo_bpm is None:
                    micros = int.from_bytes(payload, "big")
                    if micros:
                        result.tempo_bpm = round(60_000_000 / micros, 3)
                elif meta_type == 0x58 and result.time_signature is None and len(payload) >= 2:
                    result.time_signature = f"{payload[0]}/{2 ** payload[1]}"
            elif status in (0xF0, 0xF7):  # sysex
                slen, i = _read_varlen(data, i)
                i += slen
            elif event in (0x80, 0x90):  # note off / on
                key = data[i]; vel = data[i + 1]; i += 2
                if event == 0x90 and vel > 0:
                    active[(channel, key)] = (abs_ticks, vel)
                    track.channel = channel
                else:
                    start = active.pop((channel, key), None)
                    if start is not None:
                        st, vel0 = start
                        track.notes.append(
                            MidiNote(
                                time_beats=st / result.division,
                                duration_beats=max(0.0, (abs_ticks - st) / result.division),
                                key=key,
                                velocity=vel0,
                                channel=channel,
                            )
                        )
            elif event in (0xA0, 0xB0, 0xE0):  # 2-byte events
                i += 2
            elif event in (0xC0, 0xD0):        # 1-byte events
                i += 1
            else:
                i += 1
        result.tracks.append(track)
        pos = end

    result.ok = True
    return result
