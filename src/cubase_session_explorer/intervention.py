"""Controlled interventions and state<->audio observations.

The scientific unit of this project is the *observation*: a state snapshot
optionally paired with a render, and, for a controlled A/B pair, a known
:class:`StateIntervention`. Recording the intervention makes a snapshot pair a
*controlled experiment* rather than two unstructured sessions — the difference
that lets us say "this state delta caused this audio delta".

The dataset export is intentionally compatible in shape with the REAPER/
Ableton/Logic prototypes so all four DAWs can populate one corpus.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

InterventionType = Literal[
    "add_plugin", "remove_plugin", "reorder_plugin", "change_parameter",
    "alter_routing", "add_send", "change_send_level", "move_event",
    "mute_track", "change_pan", "change_volume", "alter_tempo", "alter_midi_note",
]


class InterventionTarget(BaseModel):
    entity_id: Optional[str] = None
    track: Optional[str] = None
    parameter: Optional[str] = None
    description: Optional[str] = None


class StateIntervention(BaseModel):
    intervention_id: str
    type: InterventionType
    target: InterventionTarget
    before: Any = None
    after: Any = None
    note: Optional[str] = None


class Observation(BaseModel):
    """One row of the research dataset."""

    observation_id: str
    daw: str = "cubase"
    state_snapshot: str                    # path to snapshot json
    render: Optional[str] = None           # path to audio render
    descriptors: Optional[dict] = None     # AudioDescriptorSet dump
    intervention: Optional[str] = None     # intervention_id (or None for baseline)


class InterventionExperiment(BaseModel):
    """A controlled A/B: two observations + the known intervention between them."""

    experiment_id: str
    intervention: StateIntervention
    observation_a: Observation
    observation_b: Observation
    state_delta: dict = Field(default_factory=dict)      # diff summary + lines
    audio_delta: dict = Field(default_factory=dict)      # descriptor deltas


def write_dataset(observations: list[Observation], path: str) -> str:
    """Write observations as JSONL (one JSON object per line)."""
    import os

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for obs in observations:
            fh.write(obs.model_dump_json())
            fh.write("\n")
    return path
