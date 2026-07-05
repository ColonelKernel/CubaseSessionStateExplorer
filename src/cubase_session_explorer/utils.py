"""Small, dependency-free helpers + Cubase-aware keyword classifiers.

Deliberately avoids heavy imports (audio, viz, networkx) so parsers and tests
can use it standalone. Classifiers are heuristics — every value they produce is
tagged ``inferred`` in provenance, never presented as a Cubase fact.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Optional


# --- numeric / string helpers ---------------------------------------------

def linear_to_db(value: Optional[float]) -> Optional[float]:
    """Linear gain -> dB. Returns None for non-positive/missing (‑inf is useless)."""
    if value is None or value <= 0:
        return None
    return round(20.0 * math.log10(value), 2)


def db_to_linear(db: Optional[float]) -> Optional[float]:
    if db is None:
        return None
    return round(10.0 ** (db / 20.0), 6)


def safe_float(token: Any) -> Optional[float]:
    if token is None:
        return None
    try:
        return float(token)
    except (TypeError, ValueError):
        return None


def safe_int(token: Any) -> Optional[int]:
    if token is None:
        return None
    try:
        return int(token)
    except (TypeError, ValueError):
        f = safe_float(token)
        return int(f) if f is not None else None


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: Optional[str], fallback: str = "item") -> str:
    if not text:
        return fallback
    slug = _SLUG_RE.sub("-", str(text).strip().lower()).strip("-")
    return slug or fallback


def to_pretty_json(payload: Any) -> str:
    def _default(obj: Any) -> Any:
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        if hasattr(obj, "item"):
            return obj.item()
        if hasattr(obj, "tolist"):
            return obj.tolist()
        return str(obj)

    return json.dumps(payload, indent=2, default=_default)


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- Cubase-aware heuristic classifiers ------------------------------------
# Keyword tables lean on Cubase/Steinberg stock names (Frequency, REVerence,
# Magneto, Retrologue, MonoDelay, DualFilter ...) plus generic vocabulary.

_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Vocal": ("vocal", "vox", "vocs", "lead vox", "bgv", "choir", "rap"),
    "Drums": ("drum", "kick", "snare", "hat", "hihat", "tom", "cymbal",
              "perc", "beat", "groove agent"),
    "Bass": ("bass", "sub", "808"),
    "Guitar": ("guitar", "gtr", "gizmo"),
    "Keys": ("keys", "piano", "rhodes", "organ", "synth", "pad", "retrologue",
             "halion", "padshop", "lead"),
    "Strings": ("strings", "violin", "viola", "cello", "orchestra"),
    "Brass": ("brass", "trumpet", "trombone", "horn", "sax"),
    "FX": ("riser", "impact", "sweep", "texture", "noise", "fx", "sfx"),
    "Bus": ("bus", "group", "aux", "stem", "submix", "sum"),
}

_FAMILY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "EQ": ("eq", "frequency", "studioeq", "dj-eq", "curve", "geq"),
    "Dynamics": ("comp", "compressor", "limiter", "gate", "deesser", "de-esser",
                 "maximizer", "squasher", "expander", "vintagecompressor",
                 "tube compressor", "brickwall"),
    "Ambience": ("reverb", "reverence", "verb", "delay", "monodelay", "pingpong",
                 "ping-pong", "echo", "roomworks", "room"),
    "Saturation": ("saturat", "magneto", "distortion", "amp", "quadrafuzz",
                   "drive", "tape"),
    "Modulation": ("chorus", "flanger", "phaser", "tremolo", "rotary",
                   "studiochorus", "modmachine"),
    "Filter": ("filter", "dualfilter", "wah", "morph"),
    "Pitch": ("pitch", "vari", "octaver", "harmon"),
    "Instrument": ("retrologue", "halion", "padshop", "groove agent", "prologue",
                   "mystic", "spector", "flux", "verve"),
    "Utility": ("gain", "meter", "supervision", "mixconvert", "tuner",
                "imager", "matrix"),
}


def classify_track_role(name: Optional[str]) -> str:
    if not name:
        return "Unknown"
    low = name.lower()
    for role, kws in _ROLE_KEYWORDS.items():
        if any(k in low for k in kws):
            return role
    return "Unknown"


def classify_device_family(name: Optional[str]) -> str:
    if not name:
        return "Unknown"
    low = name.lower()
    for fam, kws in _FAMILY_KEYWORDS.items():
        if any(k in low for k in kws):
            return fam
    return "Unknown"
