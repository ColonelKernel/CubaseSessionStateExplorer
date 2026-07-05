"""cubase-explorer CLI.

Commands:
  export-canonical PATH --out DIR
                                export the v0.2 canonical 5-file bundle
                                (the analyzer-facing wire format)
  ingest   PATH                 discover + fuse a bundle/file -> snapshot + report
  report   PATH                 print the extraction report only
  graph    PATH  [--out g.json] build and dump the typed graph
  snapshot PATH  --out s.json   [DEPRECATED] save a legacy 0.1.0 snapshot
  compare  A.json B.json        diff two snapshots (classified changes)
  experiment A.dawproject B.dawproject [--render-a w.wav --render-b w.wav]
                                run one controlled state<->audio experiment
  diagnose PATH [--out d.json]  grounding report for a real .dawproject
  demo     [--dir fixtures/cubase]   run the built-in end-to-end demo

The legacy repo-local ``snapshot`` format (schema 0.1.0, ``snapshot.py``) is
DEPRECATED as an interchange format in favor of ``export-canonical``; it is
kept for the internal diff/experiment workflow only.
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
    print(
        "[deprecated] `snapshot` writes the legacy repo-local 0.1.0 format; "
        "use `export-canonical` for the analyzer-facing v0.2 bundle.",
        file=sys.stderr,
    )
    session = _ingest(args.path)
    save_snapshot(session, args.out)
    print(f"Snapshot saved: {args.out}")
    return 0


def cmd_export_canonical(args) -> int:
    from .canonical_export.exporter import ExportError, export_bundle

    try:
        result = export_bundle(args.path, args.out, sanitize=not args.no_sanitize)
    except ExportError as exc:
        print(f"export-canonical failed: {exc}", file=sys.stderr)
        return 1
    report = result.validation
    snap = result.snapshot
    by_type: dict[str, int] = {}
    for entity in snap.entities:
        by_type[entity.entity_type] = by_type.get(entity.entity_type, 0) + 1
    print(f"Canonical bundle written: {result.out_dir}")
    for name in sorted(result.files):
        print(f"  {name}")
    print(f"snapshot_id: {snap.snapshot_id}")
    print(f"entities: {len(snap.entities)} {by_type}")
    print(f"relationships: {len(snap.relationships)}  "
          f"provenance records: {len(snap.provenance)}  "
          f"failures: {len(snap.failures)}")
    print(f"validation: valid={report.valid} "
          f"errors={len(report.errors)} warnings={len(report.warnings)}")
    return 0 if report.valid else 1


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


def cmd_diagnose(args) -> int:
    """Grounding harness for real Cubase exports: what parsed, what didn't."""
    from .extractors.dawproject import element_census

    print("=" * 64)
    print("DAWPROJECT GROUNDING DIAGNOSTIC")
    print("=" * 64)
    census = element_census(args.path)
    if census.get("warnings"):
        for w in census["warnings"]:
            print(f"  ! {w}")
    print(f"\nContainer members: {len(census.get('members', []))}")
    print(f"Distinct elements: {len(census.get('element_counts', {}))}")

    unhandled = census.get("unhandled_elements", {})
    if unhandled:
        print("\n⚠ UNHANDLED elements (parser hardening targets):")
        for name, count in sorted(unhandled.items(), key=lambda kv: -kv[1]):
            attrs = census["attributes_by_element"].get(name, [])
            print(f"    {name} ×{count}   attrs={attrs}")
    else:
        print("\n✓ Every element localname is handled by the parser.")

    if census.get("channel_roles"):
        print(f"\nChannel roles seen:  {census['channel_roles']}")
    if census.get("device_elements"):
        print(f"Device elements:     {census['device_elements']}")
    if census.get("unit_values"):
        print(f"Unit values seen:    {census['unit_values']}")

    # Now the actual extraction result.
    print("\n" + "-" * 64)
    session = _ingest(args.path)
    print(extraction_report(session))

    if args.out:
        import json
        with open(args.out, "w") as fh:
            json.dump({"census": census,
                       "session": session.model_dump(mode="json")}, fh, indent=2)
        print(f"\nFull diagnostic written: {args.out}")
    return 0


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

    s = sub.add_parser(
        "export-canonical",
        help="Export the v0.2 canonical 5-file bundle for the analyzer",
    )
    s.add_argument("path")
    s.add_argument("--out", required=True, help="Output bundle directory")
    s.add_argument("--no-sanitize", action="store_true",
                   help="Keep local filesystem paths in the bundle")
    s.set_defaults(func=cmd_export_canonical)

    s = sub.add_parser("ingest"); s.add_argument("path"); s.add_argument("--out")
    s.set_defaults(func=cmd_ingest)
    s = sub.add_parser("report"); s.add_argument("path"); s.set_defaults(func=cmd_report)
    s = sub.add_parser("graph"); s.add_argument("path"); s.add_argument("--out")
    s.set_defaults(func=cmd_graph)
    s = sub.add_parser(
        "snapshot",
        help="[DEPRECATED] legacy 0.1.0 snapshot; use export-canonical",
    )
    s.add_argument("path"); s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_snapshot)
    s = sub.add_parser("compare"); s.add_argument("a"); s.add_argument("b")
    s.set_defaults(func=cmd_compare)
    s = sub.add_parser("experiment"); s.add_argument("a"); s.add_argument("b")
    s.add_argument("--render-a", dest="render_a"); s.add_argument("--render-b", dest="render_b")
    s.add_argument("--out"); s.set_defaults(func=cmd_experiment)
    s = sub.add_parser("diagnose", help="Grounding report for a real .dawproject")
    s.add_argument("path"); s.add_argument("--out")
    s.set_defaults(func=cmd_diagnose)
    s = sub.add_parser("demo"); s.add_argument("--dir", default="fixtures/cubase")
    s.set_defaults(func=cmd_demo)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
