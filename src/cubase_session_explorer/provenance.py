"""Provenance and observability for every extracted Cubase value.

This generalizes the shared ``session_explorer.core.provenance`` model (see the
REAPER/Ableton/Logic prototypes) and widens it for Cubase, whose state is
spread across many partially-observable evidence surfaces. Every canonical
value can carry a :class:`Provenance` answering three questions:

    WHERE did this come from?   -> ``source`` (artifact type + locator)
    HOW sure are we?            -> ``confidence`` (0..1) and ``status``
    WHY do we believe it?       -> ``explanation``

Uncertainty is a first-class research feature here, not an error state. The UI
is expected to surface it rather than hide it.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

# How a value entered the canonical session.
ExtractionStatus = Literal[
    "observed",       # read directly from a runtime API (ground truth at capture time)
    "exported",       # read from a first-party structured export (DAWproject, MIDI, MusicXML)
    "parsed",         # deterministically parsed from a structured file
    "inferred",       # heuristic guess (keyword classifier, string proximity)
    "reconstructed",  # assembled by fusing multiple weaker signals
    "user_supplied",  # human annotation, never a DAW fact
    "unavailable",    # no extraction surface exposes this
    "conflicting",    # multiple sources disagree; see ``explanation``
]

STATUS_VALUES: tuple[str, ...] = (
    "observed", "exported", "parsed", "inferred",
    "reconstructed", "user_supplied", "unavailable", "conflicting",
)

# The evidence artifact a value came from. Mirrors the Cubase capability matrix.
SourceType = Literal[
    "cpr",              # binary Cubase project file (RIFF evidence scan)
    "dawproject",       # open XML/ZIP interchange export (Cubase 14+/15)
    "track_archive",    # Cubase Track Archive .xml export
    "preset",           # track/VST/FX-chain preset
    "midi",             # exported Standard MIDI File
    "musicxml",         # notation export
    "dorico",           # Dorico interchange
    "runtime_api",      # generic Cubase runtime observation
    "midi_remote",      # MIDI Remote API runtime capture
    "rendered_audio",   # mixdown / stem render
    "filesystem",       # file metadata (size, mtime, hash)
    "manual_annotation",# user assertion
    "fusion",           # produced by the evidence fuser from >1 source
]

QualitativeConfidence = Literal["high", "medium", "low", "none"]


def qualitative(confidence: float) -> QualitativeConfidence:
    """Bucket a numeric confidence for display."""
    if confidence >= 0.85:
        return "high"
    if confidence >= 0.5:
        return "medium"
    if confidence > 0.0:
        return "low"
    return "none"


class EvidenceSource(BaseModel):
    """Where a value came from, precisely enough to re-inspect it."""

    type: SourceType
    artifact: Optional[str] = None      # filename / bundle-relative path
    locator: Optional[str] = None       # xpath, byte offset, api path, note idx...
    evidence: Optional[str] = None      # short human description of the raw signal

    def short(self) -> str:
        bits = [self.type]
        if self.artifact:
            bits.append(self.artifact)
        if self.locator:
            bits.append(f"@{self.locator}")
        return " ".join(bits)


class Provenance(BaseModel):
    """How a value entered the canonical session, and how much to trust it."""

    status: ExtractionStatus = "parsed"
    confidence: float = 1.0
    source: Optional[EvidenceSource] = None
    explanation: Optional[str] = None
    # For 'conflicting' status: the alternative values that were rejected/kept.
    alternatives: list["ProvenancedValue"] = Field(default_factory=list)

    @property
    def label(self) -> QualitativeConfidence:
        return qualitative(self.confidence)


class ProvenancedValue(BaseModel):
    """A value fused from a single source, kept for conflict bookkeeping."""

    value: object = None
    source_type: SourceType
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------

def observed(source_type: SourceType = "runtime_api", **kw) -> Provenance:
    return Provenance(status="observed", confidence=1.0,
                      source=EvidenceSource(type=source_type, **kw))


def exported(source_type: SourceType = "dawproject", confidence: float = 1.0, **kw) -> Provenance:
    return Provenance(status="exported", confidence=confidence,
                      source=EvidenceSource(type=source_type, **kw))


def parsed(source_type: SourceType, confidence: float = 1.0, explanation: str | None = None, **kw) -> Provenance:
    return Provenance(status="parsed", confidence=confidence, explanation=explanation,
                      source=EvidenceSource(type=source_type, **kw))


def inferred(explanation: str, confidence: float = 0.5,
             source_type: SourceType = "fusion", **kw) -> Provenance:
    return Provenance(status="inferred", confidence=confidence, explanation=explanation,
                      source=EvidenceSource(type=source_type, **kw))


def unavailable(reason: str, source_type: SourceType = "fusion") -> Provenance:
    return Provenance(status="unavailable", confidence=0.0, explanation=reason,
                      source=EvidenceSource(type=source_type))


def annotation(explanation: str, confidence: float = 0.5) -> Provenance:
    return Provenance(status="user_supplied", confidence=confidence, explanation=explanation,
                      source=EvidenceSource(type="manual_annotation"))


def conflicting_note(explanation: str, confidence: float = 0.6) -> Provenance:
    return Provenance(status="conflicting", confidence=confidence, explanation=explanation,
                      source=EvidenceSource(type="fusion"))


Provenance.model_rebuild()
