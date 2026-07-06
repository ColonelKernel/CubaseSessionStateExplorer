"""MusicXML extractor — the notated / representational state layer.

Parses ``score-partwise`` MusicXML (plain ``.musicxml``/``.xml`` or compressed
``.mxl`` zip) into :class:`~cubase_session_explorer.models.ScoreState`:
parts, key/time signatures, and pitched notes **with their spelling**
(step/alter/octave).

Why spelling matters here: MIDI key 61 is one integer, but a score commits to
C#4 *or* Db4 — an interpretive act with zero acoustic consequence. Comparing the
performed layer (MIDI notes) against the notated layer (score notes) separates
ACOUSTICALLY ACTIVE state from REPRESENTATIONAL state, one of the project's
core research distinctions. :func:`compare_performance_to_score` implements a
first, deliberately modest version of that comparison.

Conservative by design: unknown elements are ignored, nothing raises, and the
extractor never claims layout/engraving detail it did not parse.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from typing import Optional
from xml.etree import ElementTree as ET

from ..models import MidiNote, ScoreNote, ScorePart, ScoreState

MUSICXML_SOURCE = "musicxml"

# Default enharmonic spelling a naive MIDI->notation mapping would choose
# (sharps, as most DAW piano-roll displays do). Divergence from THIS is what
# marks a deliberate notational interpretation.
_DEFAULT_SPELLING = {0: ("C", 0), 1: ("C", 1), 2: ("D", 0), 3: ("D", 1),
                     4: ("E", 0), 5: ("F", 0), 6: ("F", 1), 7: ("G", 0),
                     8: ("G", 1), 9: ("A", 0), 10: ("A", 1), 11: ("B", 0)}


@dataclass
class MusicXmlResult:
    ok: bool = False
    score: ScoreState = field(default_factory=ScoreState)
    warnings: list[str] = field(default_factory=list)


def _load_root(path: str) -> tuple[Optional[ET.Element], list[str]]:
    warnings: list[str] = []
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        return None, [f"Cannot read MusicXML: {exc}"]

    if data[:2] == b"PK":  # .mxl compressed container
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = [n for n in zf.namelist()
                         if n.endswith((".xml", ".musicxml"))
                         and not n.startswith("META-INF")]
                if not names:
                    return None, ["No score XML inside .mxl container."]
                data = zf.read(names[0])
        except zipfile.BadZipFile as exc:
            return None, [f"Bad .mxl container: {exc}"]

    try:
        return ET.fromstring(data), warnings
    except ET.ParseError as exc:
        return None, [f"MusicXML parse failed: {exc}"]


def extract(path: str) -> MusicXmlResult:
    result = MusicXmlResult()
    root, warnings = _load_root(path)
    result.warnings.extend(warnings)
    if root is None:
        return result
    if root.tag not in ("score-partwise", "score-timewise"):
        result.warnings.append(f"Unexpected root <{root.tag}>; expected score-partwise.")
        return result
    if root.tag == "score-timewise":
        result.warnings.append("score-timewise not supported in v0; parts skipped.")
        result.ok = True
        result.score.present = True
        return result

    score = result.score
    score.present = True

    # part-list: names / instruments
    part_names: dict[str, dict] = {}
    for sp in root.iter("score-part"):
        pid = sp.get("id") or f"P{len(part_names) + 1}"
        name_el = sp.find("part-name")
        instr_el = sp.find(".//instrument-name")
        part_names[pid] = {
            "name": name_el.text if name_el is not None else None,
            "instrument": instr_el.text if instr_el is not None else None,
        }

    for part_el in root.findall("part"):
        pid = part_el.get("id") or f"P{len(score.parts) + 1}"
        meta = part_names.get(pid, {})
        measures = part_el.findall("measure")
        score.parts.append(ScorePart(
            part_id=pid, name=meta.get("name"),
            instrument=meta.get("instrument"),
            measure_count=len(measures),
        ))
        for measure in measures:
            mnum = _int_or_none(measure.get("number"))
            for attrs in measure.findall("attributes"):
                key_el = attrs.find("key")
                if key_el is not None:
                    fifths = _int_or_none(_text(key_el, "fifths"))
                    mode_el = key_el.find("mode")
                    score.key_signatures.append({
                        "fifths": fifths,
                        "mode": mode_el.text if mode_el is not None else None,
                        "measure": mnum, "part_id": pid,
                    })
                time_el = attrs.find("time")
                if time_el is not None:
                    score.time_signatures.append({
                        "numerator": _int_or_none(_text(time_el, "beats")),
                        "denominator": _int_or_none(_text(time_el, "beat-type")),
                        "measure": mnum, "part_id": pid,
                    })
            for note_el in measure.findall("note"):
                if note_el.find("rest") is not None:
                    continue
                pitch = note_el.find("pitch")
                if pitch is None:
                    continue  # unpitched / percussion: out of v0 scope
                step = _text(pitch, "step")
                if step is None:
                    continue
                score.pitched_notes.append(ScoreNote(
                    step=step,
                    alter=_int_or_none(_text(pitch, "alter")) or 0,
                    octave=_int_or_none(_text(pitch, "octave")) or 4,
                    duration_divisions=_int_or_none(_text(note_el, "duration")),
                    voice=_int_or_none(_text(note_el, "voice")),
                    measure=mnum, part_id=pid,
                ))

    score.notes = (f"Parsed {len(score.parts)} part(s), "
                   f"{len(score.pitched_notes)} pitched note(s) from MusicXML.")
    result.ok = True
    return result


def _text(el: ET.Element, tag: str) -> Optional[str]:
    child = el.find(tag)
    return child.text if child is not None else None


def _int_or_none(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Performed vs. notated comparison
# ---------------------------------------------------------------------------

def compare_performance_to_score(midi_notes: list[MidiNote],
                                 score: ScoreState) -> dict:
    """Relate performed MIDI events to their notational interpretation.

    Returns a modest, honest divergence report:

    * ``matched`` — MIDI keys that appear (as pitch classes+octaves) in the score
    * ``enharmonic_reinterpretations`` — score spellings that differ from the
      default (sharp-preferring) spelling of the same MIDI key: pure
      REPRESENTATIONAL state, acoustically inert
    * ``unmatched_*`` — content present in only one layer

    Matching is by multiset of MIDI key numbers (order-independent); timing
    alignment is out of scope for v0 and stated as such.
    """
    report: dict = {
        "n_midi_notes": len(midi_notes),
        "n_score_notes": len(score.pitched_notes),
        "matched": 0,
        "enharmonic_reinterpretations": [],
        "unmatched_midi_keys": [],
        "unmatched_score_notes": [],
        "note": "Match is by pitch multiset; timing alignment out of scope in v0.",
    }
    remaining = list(score.pitched_notes)
    for mn in midi_notes:
        found = None
        for sn in remaining:
            if sn.midi_key == mn.key:
                found = sn
                break
        if found is None:
            report["unmatched_midi_keys"].append(mn.key)
            continue
        remaining.remove(found)
        report["matched"] += 1
        dstep, dalter = _DEFAULT_SPELLING[mn.key % 12]
        if (found.step, found.alter) != (dstep, dalter):
            default = ScoreNote(step=dstep, alter=dalter, octave=(mn.key // 12) - 1)
            report["enharmonic_reinterpretations"].append({
                "midi_key": mn.key,
                "default_spelling": default.spelled,
                "score_spelling": found.spelled,
                "acoustically_inert": True,
            })
    report["unmatched_score_notes"] = [sn.spelled for sn in remaining]
    return report
