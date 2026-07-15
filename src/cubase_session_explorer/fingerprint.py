"""Structural session fingerprints + cross-DAW similarity / retrieval.

A *fingerprint* is a compact, **interpretable** summary of a session's
production structure — track-type proportions, device/effect balance, routing
and send density, automation density, and observability. Every feature is a
canonical, DAW-agnostic concept (no Cubase-only fields), so a Cubase fingerprint
and a REAPER/Ableton/Logic fingerprint are directly comparable — the substrate
for the research questions "retrieve sessions with a similar production strategy"
and "which structural axis explains the difference".

Design choices that matter:

* **Scale-invariant.** Features are ratios/densities in ~[0,1], not raw counts,
  so a 40-track mix and a 6-track sketch with the same *strategy* land close
  rather than being separated by size. Raw counts are kept alongside, for
  interpretation only.
* **Interpretable similarity.** ``similarity`` blends cosine over the numeric
  feature vector with Jaccard over the device-family / track-type bags;
  ``feature_deltas`` says *which* axes drive a difference. Nothing here is a
  black box.
* **Honest about observability.** Coverage and unknown-state density are
  first-class features — comparing what each DAW *lets you see* is itself a
  research axis, not noise to hide.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .models import SessionState

# Fixed feature order => stable vectors across sessions and DAWs. Append only.
FEATURE_KEYS: tuple[str, ...] = (
    "frac_audio", "frac_instrument", "frac_group", "frac_fx", "frac_folder",
    "device_density", "instrument_device_frac", "effect_device_frac",
    "send_density", "route_density", "automation_density",
    "has_master", "has_tempo_map", "has_score",
    "marker_density", "observability", "unknown_density",
)


@dataclass
class SessionFingerprint:
    daw: str
    session_id: str
    counts: dict[str, int] = field(default_factory=dict)      # raw, for humans
    features: dict[str, float] = field(default_factory=dict)  # the [0,1] vector
    device_families: dict[str, int] = field(default_factory=dict)
    track_types: dict[str, int] = field(default_factory=dict)

    def vector(self) -> list[float]:
        return [float(self.features.get(k, 0.0)) for k in FEATURE_KEYS]

    def to_dict(self) -> dict:
        return {
            "daw": self.daw, "session_id": self.session_id,
            "counts": self.counts, "features": self.features,
            "device_families": self.device_families, "track_types": self.track_types,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionFingerprint":
        return cls(
            daw=d.get("daw", "unknown"),
            session_id=d.get("session_id", "?"),
            counts=d.get("counts", {}),
            features=d.get("features", {}),
            device_families=d.get("device_families", {}),
            track_types=d.get("track_types", {}),
        )


def _ratio(n: float, d: float) -> float:
    return 0.0 if d <= 0 else n / d


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def fingerprint(session: SessionState) -> SessionFingerprint:
    """Summarize a session into a scale-invariant structural fingerprint."""
    # Non-master lanes. Everything (denominator AND device/send numerators) is
    # computed over these, consistently: the master bus is deliberately excluded
    # so a mastering chain can't dominate a small session, and `has_master`
    # carries its presence. Mixing master into only one side broke this.
    lanes = list(session.tracks) + list(session.groups) + list(session.return_tracks)

    # --- raw structural counts (interpretable) -----------------------------
    audio = [t for t in session.tracks if t.track_type == "audio"]
    instrument = [t for t in session.tracks
                  if t.track_type in ("midi", "instrument")]
    n_groups = len(session.groups)
    n_fx = len(session.return_tracks)
    n_folders = len(session.folders)
    total = max(len(session.tracks) + n_groups + n_fx, 1)

    devices = [d for t in lanes for d in t.devices]  # non-master, matches `total`
    n_instr_dev = sum(1 for d in devices if d.device_type == "instrument")
    n_fx_dev = len(devices) - n_instr_dev  # device_type None counts as effect
    n_dev = len(devices)
    n_sends = sum(len(t.sends) for t in lanes)
    n_routes = len(session.routes)
    n_auto = len(session.automation)
    n_markers = len(session.musical_structure.markers)
    coverage = session.capture.coverage_percent or 0.0  # Optional; None => 0.0

    counts = {
        "tracks": len(session.tracks), "groups": n_groups, "fx_channels": n_fx,
        "folders": n_folders, "devices": n_dev, "instrument_devices": n_instr_dev,
        "effect_devices": n_fx_dev, "sends": n_sends, "routes": n_routes,
        "automation_lanes": n_auto, "markers": n_markers,
        "media": len(session.media), "unknown_state": len(session.unknown_state),
        "has_master": 1 if session.master_track else 0,
    }

    # --- device-family / track-type bags (for Jaccard overlap) -------------
    families: dict[str, int] = {}
    for d in devices:
        fam = (d.device_family or ("Instrument" if d.device_type == "instrument"
                                   else "Effect"))
        families[fam] = families.get(fam, 0) + 1
    track_types: dict[str, int] = {}
    for t in lanes:
        track_types[t.track_type] = track_types.get(t.track_type, 0) + 1

    # --- normalized feature vector [0,1] -----------------------------------
    features = {
        "frac_audio": _ratio(len(audio), total),
        "frac_instrument": _ratio(len(instrument), total),
        "frac_group": _ratio(n_groups, total),
        "frac_fx": _ratio(n_fx, total),
        "frac_folder": _clamp01(_ratio(n_folders, total)),  # density; can saturate
        "device_density": _clamp01(_ratio(n_dev, total) / 4.0),   # ~4 inserts/track saturates
        "instrument_device_frac": _ratio(n_instr_dev, max(n_dev, 1)),
        "effect_device_frac": _ratio(n_fx_dev, max(n_dev, 1)),
        "send_density": _clamp01(_ratio(n_sends, total)),
        "route_density": _clamp01(_ratio(n_routes, total)),
        "automation_density": _clamp01(_ratio(n_auto, total)),
        "has_master": 1.0 if session.master_track else 0.0,
        "has_tempo_map": 1.0 if len(session.musical_structure.tempo_map) > 1 else 0.0,
        "has_score": 1.0 if session.score_state.present else 0.0,
        "marker_density": _clamp01(_ratio(n_markers, total)),
        "observability": _clamp01(_ratio(coverage, 100.0)),
        "unknown_density": _clamp01(_ratio(len(session.unknown_state), total)),
    }

    return SessionFingerprint(
        daw=session.adapter.get("daw", "cubase"),
        session_id=session.project.project_name or "session",
        counts=counts, features=features,
        device_families=families, track_types=track_types,
    )


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("vectors differ in length")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 1.0 if na == 0.0 and nb == 0.0 else 0.0
    return dot / (na * nb)


def bag_jaccard(a: dict[str, int], b: dict[str, int]) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 1.0
    inter = sum(min(a.get(k, 0), b.get(k, 0)) for k in keys)
    union = sum(max(a.get(k, 0), b.get(k, 0)) for k in keys)
    return _ratio(inter, union)


def similarity(a: SessionFingerprint, b: SessionFingerprint,
               w_numeric: float = 0.5, w_family: float = 0.3,
               w_tracktype: float = 0.2) -> float:
    """Blend cosine (numeric strategy) with Jaccard over device-family and
    track-type bags. Returns [0,1]; weights are documented, not magic."""
    num = cosine(a.vector(), b.vector())
    fam = bag_jaccard(a.device_families, b.device_families)
    tt = bag_jaccard(a.track_types, b.track_types)
    total_w = w_numeric + w_family + w_tracktype
    return (w_numeric * num + w_family * fam + w_tracktype * tt) / total_w


def feature_deltas(a: SessionFingerprint, b: SessionFingerprint,
                   top: int = 5) -> list[dict]:
    """Which feature axes most explain a difference (interpretability)."""
    rows = []
    for k in FEATURE_KEYS:
        va, vb = a.features.get(k, 0.0), b.features.get(k, 0.0)
        rows.append({"feature": k, "a": round(va, 3), "b": round(vb, 3),
                     "abs_delta": round(abs(va - vb), 3)})
    rows.sort(key=lambda r: r["abs_delta"], reverse=True)
    return rows[:top]


def retrieve_similar(query: SessionFingerprint,
                     corpus: list[SessionFingerprint],
                     k: int = 5,
                     cross_daw: bool = True) -> list[dict]:
    """Rank corpus fingerprints by similarity to ``query``.

    ``cross_daw=True`` keeps every DAW in the ranking (the point of a shared
    representation); set False to restrict to the query's own DAW.
    """
    scored = []
    for fp in corpus:
        # Drop the query's OWN entry, but only when it is genuinely the same
        # session — same name+daw AND identical features. Two distinct sessions
        # that merely share a project name (e.g. "Untitled") must NOT be dropped.
        if (fp.session_id == query.session_id and fp.daw == query.daw
                and fp.features == query.features):
            continue
        if not cross_daw and fp.daw != query.daw:
            continue
        scored.append({
            "session_id": fp.session_id, "daw": fp.daw,
            "similarity": round(similarity(query, fp), 4),
            "top_differences": feature_deltas(query, fp, top=3),
        })
    scored.sort(key=lambda r: r["similarity"], reverse=True)
    return scored[:k]


# ---------------------------------------------------------------------------
# Corpus I/O (cross-DAW)
# ---------------------------------------------------------------------------

def fingerprint_snapshot(path: str) -> SessionFingerprint:
    """Load a saved snapshot JSON and fingerprint it."""
    from .snapshot import load_snapshot
    return fingerprint(load_snapshot(path))


def load_corpus(paths: list[str]) -> list[SessionFingerprint]:
    """Build a corpus from a mix of inputs. Each path may be:

    * an ``observations.jsonl`` (rows with a ``fingerprint`` or ``state_snapshot``),
    * a saved fingerprint ``.json`` (a ``SessionFingerprint.to_dict()``),
    * or a saved snapshot ``.json`` (fingerprinted on load).

    Foreign-DAW rows carrying a precomputed ``fingerprint`` are included as-is —
    that is exactly how a REAPER/Ableton/Logic session enters the shared corpus
    without us needing their parser.
    """
    import json
    import os

    corpus: list[SessionFingerprint] = []
    for p in paths:
        if os.path.isdir(p):
            corpus.extend(load_corpus(
                [os.path.join(p, n) for n in sorted(os.listdir(p))
                 if n.endswith((".json", ".jsonl"))]))
            continue
        if p.endswith(".jsonl"):
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    fp = _fp_from_row(row, p)
                    if fp is not None:
                        corpus.append(fp)
            continue
        # a .json: either a fingerprint dict or a snapshot
        try:
            with open(p, encoding="utf-8") as fh:
                obj = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict) and "features" in obj and "daw" in obj:
            corpus.append(SessionFingerprint.from_dict(obj))
        else:
            try:
                corpus.append(fingerprint_snapshot(p))
            except Exception:  # noqa: BLE001 — a non-snapshot json is fine to skip
                continue
    return corpus


def _fp_from_row(row: dict, source: str) -> Optional[SessionFingerprint]:
    if isinstance(row.get("fingerprint"), dict):
        return SessionFingerprint.from_dict(row["fingerprint"])
    snap = row.get("state_snapshot")
    if snap:
        import os
        # `state_snapshot` may be absolute, CWD-relative (as written), or
        # relative to the jsonl. Try the obvious bases and walk a few parents up
        # (handles a jsonl living under data/observations/ while snapshots live
        # under data/snapshots/). First existing path wins.
        base = os.path.dirname(os.path.abspath(source))
        candidates = [snap, os.path.join(base, snap)]
        up = base
        for _ in range(3):
            up = os.path.dirname(up)
            candidates.append(os.path.join(up, snap))
        for path in candidates:
            if not os.path.exists(path):
                continue
            try:
                return fingerprint_snapshot(path)
            except Exception:  # noqa: BLE001 — non-snapshot json: skip
                continue
    return None


# ---------------------------------------------------------------------------
# CLI (entry point: session-fingerprint)
# ---------------------------------------------------------------------------

def _cli(argv: Optional[list[str]] = None) -> int:
    import argparse
    import json

    from .fusion import ingest

    parser = argparse.ArgumentParser(
        prog="session-fingerprint",
        description="Structural fingerprint + cross-DAW similarity / retrieval")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("show", help="Fingerprint one bundle/file")
    p.add_argument("path")

    p = sub.add_parser("similar", help="Retrieve sessions similar to a query")
    p.add_argument("query", help="bundle/file to fingerprint as the query")
    p.add_argument("corpus", nargs="+",
                   help="snapshot/fingerprint .json, observations.jsonl, or dirs")
    p.add_argument("-k", type=int, default=5)
    p.add_argument("--same-daw-only", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "show":
        fp = fingerprint(ingest(args.path).session)
        print(json.dumps(fp.to_dict(), indent=2))
        return 0

    query = fingerprint(ingest(args.query).session)
    corpus = load_corpus(args.corpus)
    results = retrieve_similar(query, corpus, k=args.k,
                               cross_daw=not args.same_daw_only)
    print(f"Query: {query.session_id} ({query.daw})")
    print(f"Corpus: {len(corpus)} fingerprint(s)")
    for r in results:
        diffs = ", ".join(f"{d['feature']} {d['a']}->{d['b']}"
                          for d in r["top_differences"])
        print(f"  {r['similarity']:.4f}  {r['session_id']:24s} [{r['daw']}]  Δ {diffs}")
    if not results:
        print("  (no comparable sessions in corpus)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
