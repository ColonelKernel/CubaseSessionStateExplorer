"""cubase-explorer CLI.

Commands:
  ingest   PATH                 discover + fuse a bundle/file -> snapshot + report
  report   PATH                 print the extraction report only
  graph    PATH  [--out g.json] build and dump the typed graph
  snapshot PATH  --out s.json   save a canonical snapshot
  compare  A.json B.json        diff two snapshots (classified changes)
  experiment A.dawproject B.dawproject [--render-a w.wav --render-b w.wav]
                                run one controlled state<->audio experiment
  demo     [--dir fixtures/cubase]   run the built-in end-to-end demo
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from .audio_descriptors import descriptor_delta, extract as extract_audio
from .diff import diff_sessions
from .fusion import ingest as fuse_ingest
from .graph_builder import build_graph_dict
from .intervention import (
    InterventionExperiment,
    InterventionTarget,
    Observation,
    StateIntervention,
    write_dataset,
)
from .report import extraction_report
from .snapshot import load_snapshot, save_snapshot


def _ingest(path: str):
    res = fuse_ingest(path)
    return res.session


def cmd_ingest(args) -> int:
    session = _ingest(args.path)
    out = args.out or os.path.join("data", "snapshots",
                                   f"{session.project.project_name}.snapshot.json")
    save_snapshot(session, out)
    print(extraction_report(session))
    print(f"\nSnapshot saved: {out}")
    return 0


def cmd_report(args) -> int:
    print(extraction_report(_ingest(args.path)))
    return 0


def cmd_graph(args) -> int:
    g = build_graph_dict(_ingest(args.path))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(g, fh, indent=2)
        print(f"Graph ({g['metadata']['n_nodes']} nodes, "
              f"{g['metadata']['n_edges']} edges) -> {args.out}")
    else:
        print(json.dumps(g["metadata"], indent=2))
    return 0


def cmd_snapshot(args) -> int:
    session = _ingest(args.path)
    save_snapshot(session, args.out)
    print(f"Snapshot saved: {args.out}")
    return 0


def cmd_compare(args) -> int:
    a = load_snapshot(args.a)
    b = load_snapshot(args.b)
    result = diff_sessions(a, b)
    print(f"Diff: {a.project.project_name} -> {b.project.project_name}")
    print(f"Summary by category: {result.summary()}\n")
    for line in result.narrative():
        print(" ", line)
    if not result.changes:
        print("  (no changes detected)")
    return 0


def cmd_experiment(args) -> int:
    os.makedirs("data/snapshots", exist_ok=True)
    os.makedirs("data/observations", exist_ok=True)

    sa = _ingest(args.a)
    sb = _ingest(args.b)
    snap_a = save_snapshot(sa, f"data/snapshots/{sa.project.project_name}.json")
    snap_b = save_snapshot(sb, f"data/snapshots/{sb.project.project_name}.json")

    result = diff_sessions(sa, sb)

    obs_a = Observation(observation_id="obs-a", state_snapshot=snap_a, render=args.render_a)
    obs_b = Observation(observation_id="obs-b", state_snapshot=snap_b, render=args.render_b)

    audio_delta = {}
    if args.render_a and args.render_b and os.path.exists(args.render_a) and os.path.exists(args.render_b):
        da = extract_audio(args.render_a, source_id="obs-a")
        db = extract_audio(args.render_b, source_id="obs-b")
        obs_a.descriptors = da.model_dump()
        obs_b.descriptors = db.model_dump()
        audio_delta = descriptor_delta(da, db)

    # Infer the intervention from the dominant classified change.
    intervention = _infer_intervention(result)

    exp = InterventionExperiment(
        experiment_id="exp-001",
        intervention=intervention,
        observation_a=obs_a,
        observation_b=obs_b,
        state_delta={"summary": result.summary(), "changes": result.narrative()},
        audio_delta=audio_delta,
    )
    out = args.out or "data/observations/experiment.json"
    with open(out, "w") as fh:
        fh.write(exp.model_dump_json(indent=2))
    write_dataset([obs_a, obs_b], "data/observations/observations.jsonl")

    # Pretty print
    print("=" * 64)
    print("CONTROLLED STATE <-> AUDIO EXPERIMENT")
    print("=" * 64)
    print(f"\nIntervention: {intervention.type}")
    if intervention.target.description:
        print(f"  {intervention.target.description}")
    print(f"  before={intervention.before!r}  after={intervention.after!r}\n")
    print("STATE DELTA:")
    print(f"  categories: {result.summary()}")
    for line in result.narrative():
        print("   ", line)
    print("\nAUDIO DELTA (baseline descriptors, b - a):")
    if audio_delta:
        for f, v in audio_delta.items():
            print(f"    {f:26s} {v['a']}  ->  {v['b']}   (Δ {v['delta']:+})")
    else:
        print("    (no renders provided or unreadable)")
    print(f"\nExperiment written: {out}")
    print("Dataset row(s):     data/observations/observations.jsonl")
    return 0


def _infer_intervention(result) -> StateIntervention:
    cats = result.summary()
    # priority: PARAMETER > ROUTING > STRUCTURAL > MIXER > else
    order = ["PARAMETER", "ROUTING", "STRUCTURAL", "MIXER", "TEMPORAL", "MUSICAL", "UNKNOWN"]
    dominant = next((c for c in order if cats.get(c)), None)
    type_map = {
        "PARAMETER": "change_parameter", "ROUTING": "add_send",
        "STRUCTURAL": "add_plugin", "MIXER": "change_volume",
        "TEMPORAL": "move_event", "MUSICAL": "alter_tempo", "UNKNOWN": "change_parameter",
    }
    change = next((c for c in result.changes if c.category == dominant), None)
    return StateIntervention(
        intervention_id="int-001",
        type=type_map.get(dominant, "change_parameter"),
        target=InterventionTarget(description=change.target if change else "n/a"),
        before=change.before if change else None,
        after=change.after if change else None,
        note=change.as_line() if change else None,
    )


def cmd_demo(args) -> int:
    d = args.dir
    df_a = os.path.join(d, "dualfilter_a.dawproject")
    if not os.path.exists(df_a):
        print(f"Fixtures not found in {d}. Run: python tools/make_fixtures.py {d}")
        return 1
    ns = argparse.Namespace(
        a=df_a, b=os.path.join(d, "dualfilter_b.dawproject"),
        render_a=os.path.join(d, "dualfilter_a.wav"),
        render_b=os.path.join(d, "dualfilter_b.wav"),
        out="data/observations/demo_dualfilter.json")
    print("\n### DEMO 1 — DualFilter Position parameter change ###\n")
    cmd_experiment(ns)
    ns2 = argparse.Namespace(
        a=os.path.join(d, "routing_a.dawproject"),
        b=os.path.join(d, "routing_b.dawproject"),
        render_a=os.path.join(d, "routing_a.wav"),
        render_b=os.path.join(d, "routing_b.wav"),
        out="data/observations/demo_routing.json")
    print("\n\n### DEMO 2 — added reverb send (routing change) ###\n")
    cmd_experiment(ns2)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cubase-explorer",
                                description="Cubase Session State Explorer")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest"); s.add_argument("path"); s.add_argument("--out")
    s.set_defaults(func=cmd_ingest)
    s = sub.add_parser("report"); s.add_argument("path"); s.set_defaults(func=cmd_report)
    s = sub.add_parser("graph"); s.add_argument("path"); s.add_argument("--out")
    s.set_defaults(func=cmd_graph)
    s = sub.add_parser("snapshot"); s.add_argument("path"); s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_snapshot)
    s = sub.add_parser("compare"); s.add_argument("a"); s.add_argument("b")
    s.set_defaults(func=cmd_compare)
    s = sub.add_parser("experiment"); s.add_argument("a"); s.add_argument("b")
    s.add_argument("--render-a", dest="render_a"); s.add_argument("--render-b", dest="render_b")
    s.add_argument("--out"); s.set_defaults(func=cmd_experiment)
    s = sub.add_parser("demo"); s.add_argument("--dir", default="fixtures/cubase")
    s.set_defaults(func=cmd_demo)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
