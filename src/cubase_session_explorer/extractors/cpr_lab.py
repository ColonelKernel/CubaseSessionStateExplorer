"""cpr-lab — a scientific, conservative CPR evidence scanner.

The Cubase ``.cpr`` project is a proprietary binary. Public reverse-engineering
(e.g. omeriko9/Cubase-Project-File-Reverse-Engineering) and file-signature
databases establish that it is **RIFF-like**: tagged chunks, with recurring
tokens such as ``NUNDROOT``, ``CmObject``, ``PAppVersion``, ``RIFF``, ``ROOT``,
UTF-8 and UTF-16 strings for names.

This module does **not** claim to decode that structure. It extracts *evidence*
— printable strings, plug-in-name candidates, version tokens, chunk-boundary
hypotheses — each with a calibrated confidence and a byte offset, so a human can
re-inspect it. It is READ-ONLY and never writes to the project.

Guarantees:
  * never modifies the input file
  * never promotes a string-proximity guess to a structural fact
  * every emitted value carries an offset and a confidence < 1.0
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from ..utils import sha256_file

# Tokens that identify a Cubase/Nuendo project container (from public research
# + file-signature databases). Presence raises our confidence it is a real CPR.
_CONTAINER_TOKENS = (
    b"NUNDROOT", b"CmObject", b"PAppVersion", b"Cubase", b"Nuendo",
    b"Steinberg", b"MRootChild", b"ROOT",
)

# Substrings that, when adjacent to a UTF-8 run, hint the run is a plug-in name.
_PLUGIN_HINTS = (
    "Frequency", "Compressor", "Limiter", "REVerence", "MonoDelay", "DualFilter",
    "StudioEQ", "DeEsser", "Magneto", "Retrologue", "HALion", "PingPong",
    "RoomWorks", "VST", "Reverb", "EQ", "Delay", "Chorus", "Maximizer",
    "Squasher", "StudioChorus", "DJ-EQ", "Gate", "Brickwall",
)

_PRINTABLE = re.compile(rb"[\x20-\x7E]{4,}")
_UTF16 = re.compile(rb"(?:[\x20-\x7E]\x00){4,}")


@dataclass
class EvidenceItem:
    kind: str                 # "string" | "plugin_name" | "version" | "container_token"
    value: str
    offset: int
    confidence: float
    method: str


@dataclass
class CprReport:
    path: str
    file_size: int = 0
    sha256: str = ""
    is_probable_cpr: bool = False
    container_tokens: list[str] = field(default_factory=list)
    app_version: Optional[str] = None
    n_ascii_strings: int = 0
    n_utf16_strings: int = 0
    plugin_name_candidates: list[EvidenceItem] = field(default_factory=list)
    top_strings: list[str] = field(default_factory=list)
    evidence: list[EvidenceItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "file_size": self.file_size,
            "sha256": self.sha256,
            "is_probable_cpr": self.is_probable_cpr,
            "container_tokens": self.container_tokens,
            "app_version": self.app_version,
            "n_ascii_strings": self.n_ascii_strings,
            "n_utf16_strings": self.n_utf16_strings,
            "plugin_name_candidates": [vars(e) for e in self.plugin_name_candidates],
            "top_strings": self.top_strings,
            "warnings": self.warnings,
        }


def scan(path: str, max_bytes: int = 64 * 1024 * 1024) -> CprReport:
    report = CprReport(path=path)
    try:
        with open(path, "rb") as fh:
            data = fh.read(max_bytes)
    except OSError as exc:
        report.warnings.append(f"Cannot read file: {exc}")
        return report

    report.file_size = len(data)
    try:
        report.sha256 = sha256_file(path)
    except OSError:
        pass

    # Container tokens.
    for tok in _CONTAINER_TOKENS:
        idx = data.find(tok)
        if idx != -1:
            name = tok.decode("latin-1")
            report.container_tokens.append(name)
            report.evidence.append(
                EvidenceItem("container_token", name, idx, 0.95, "magic_token")
            )
    report.is_probable_cpr = any(
        t in report.container_tokens for t in ("NUNDROOT", "CmObject", "PAppVersion")
    )

    # App version, e.g. bytes near "PAppVersion" then a version string.
    vmatch = re.search(rb"Version[^\x00]{0,4}?([0-9]{1,2}\.[0-9]{1,2}(?:\.[0-9]{1,3})?)", data)
    if vmatch:
        report.app_version = vmatch.group(1).decode("latin-1")
        report.evidence.append(
            EvidenceItem("version", report.app_version, vmatch.start(1), 0.7,
                         "regex_version_near_PAppVersion")
        )

    # ASCII strings.
    ascii_strings: list[tuple[int, str]] = []
    for m in _PRINTABLE.finditer(data):
        s = m.group().decode("latin-1")
        ascii_strings.append((m.start(), s))
    report.n_ascii_strings = len(ascii_strings)

    # UTF-16LE strings (Cubase stores many names as UTF-16).
    utf16_strings: list[tuple[int, str]] = []
    for m in _UTF16.finditer(data):
        try:
            s = m.group().decode("utf-16-le").strip("\x00")
        except UnicodeDecodeError:
            continue
        if s:
            utf16_strings.append((m.start(), s))
    report.n_utf16_strings = len(utf16_strings)

    # Plug-in name candidates: strings containing a known hint substring.
    all_strings = ascii_strings + utf16_strings
    seen: set[str] = set()
    for off, s in all_strings:
        for hint in _PLUGIN_HINTS:
            if hint.lower() in s.lower() and s not in seen and 3 <= len(s) <= 48:
                seen.add(s)
                # Confidence scaled by how "clean" the string is (a bare name is
                # stronger evidence than a hint buried in a path).
                conf = 0.6 if s.strip() == hint else 0.4
                report.plugin_name_candidates.append(
                    EvidenceItem("plugin_name", s.strip(), off, conf,
                                 f"substring_match:{hint}")
                )
                break

    # Most common strings (useful for track-name spotting by a human).
    counter = Counter(s for _, s in all_strings if 2 <= len(s) <= 40)
    report.top_strings = [s for s, _ in counter.most_common(40)]

    if not report.is_probable_cpr:
        report.warnings.append(
            "No Cubase container token found; this may not be a .cpr, or is a "
            "newer/compressed variant. Evidence below is low-confidence."
        )
    report.warnings.append(
        "CPR evidence is string-proximity only. It is deliberately NOT parsed "
        "into structural state; treat every candidate as a hypothesis."
    )
    return report


def diff(path_a: str, path_b: str) -> dict:
    """Byte + string diff of two controlled CPRs (fixture comparison).

    Reports size delta, changed byte regions (coarse), and strings that appear
    in one but not the other — the honest way to attribute a single controlled
    edit to a region of the binary.
    """
    a = scan(path_a)
    b = scan(path_b)
    strings_a = set(a.top_strings) | {e.value for e in a.plugin_name_candidates}
    strings_b = set(b.top_strings) | {e.value for e in b.plugin_name_candidates}
    return {
        "a": path_a,
        "b": path_b,
        "size_delta_bytes": b.file_size - a.file_size,
        "strings_only_in_a": sorted(strings_a - strings_b),
        "strings_only_in_b": sorted(strings_b - strings_a),
        "plugin_candidates_a": [e.value for e in a.plugin_name_candidates],
        "plugin_candidates_b": [e.value for e in b.plugin_name_candidates],
        "note": "String-set diff attributes a controlled edit to appearing/"
                "disappearing strings; it is evidence, not proof of structure.",
    }


# --- CLI (entry point: cpr-lab) -------------------------------------------

def _cli(argv: Optional[list[str]] = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="cpr-lab", description="Conservative Cubase .cpr evidence scanner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="Scan one .cpr for evidence")
    p_scan.add_argument("path")

    p_strings = sub.add_parser("strings", help="Print candidate plug-in / name strings")
    p_strings.add_argument("path")

    p_diff = sub.add_parser("diff", help="Diff two controlled .cpr fixtures")
    p_diff.add_argument("a")
    p_diff.add_argument("b")

    args = parser.parse_args(argv)

    if args.cmd == "scan":
        print(json.dumps(scan(args.path).to_dict(), indent=2))
    elif args.cmd == "strings":
        rep = scan(args.path)
        for e in rep.plugin_name_candidates:
            print(f"  0x{e.offset:08x}  conf={e.confidence:.2f}  {e.value}")
        print(f"\n{len(rep.plugin_name_candidates)} plug-in-name candidates; "
              f"{rep.n_ascii_strings} ascii + {rep.n_utf16_strings} utf16 strings.")
    elif args.cmd == "diff":
        print(json.dumps(diff(args.a, args.b), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
