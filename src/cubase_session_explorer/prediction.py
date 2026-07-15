"""State -> acoustic prediction: baselines + an honest evaluation harness.

This is deliberately NOT a trained model making real claims — with a handful of
synthetic fixtures that would be overselling. It is the *scaffold* the research
question needs: a precise task definition, two interpretable baselines, a proper
leave-one-out evaluation with a skill-vs-mean metric, and a companion
intervention-effect analysis. It runs end to end today and scales unchanged when
real state/audio pairs arrive.

Two complementary views, because they answer different questions:

* **Structural regression** (fingerprint -> coarse descriptors) — can *between-
  session* acoustic character be predicted from structure alone?
* **Intervention effects** (intervention type -> measured acoustic delta) — this
  captures *within-A/B* parameter changes that a structural fingerprint is blind
  to (e.g. a DualFilter Position edit does not change any structural feature, yet
  changes the audio). Surfacing that blindness is itself a finding: it motivates
  adding parameter-level state to the representation, not just structure.

Honesty rails: every report states the sample size and the SCAFFOLD caveat, the
skill metric is relative to a mean baseline (so "structure helps" must be earned
even on toy data), and nothing here claims perceptual truth.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median
from typing import Callable, Optional

from .audio_descriptors import AudioDescriptorSet
from .fingerprint import SessionFingerprint, similarity

# Scalar descriptors we treat as regression targets (skip ids/paths/list-valued).
NUMERIC_TARGETS: tuple[str, ...] = (
    "rms_mean", "peak_amplitude", "crest_factor_db",
    "spectral_centroid_mean", "spectral_bandwidth_mean", "spectral_rolloff_mean",
    "zero_crossing_rate_mean", "onset_rate_hz", "stereo_width_proxy",
    "dynamic_range_db", "integrated_loudness_lufs",
)


def descriptor_targets(dset: AudioDescriptorSet) -> dict[str, float]:
    """Pull available scalar targets from a descriptor set.

    Skips None, booleans, and NON-FINITE values: a silent/near-silent render can
    make integrated loudness ``-inf`` or RMS ``nan``, and a single non-finite
    target would silently poison every pooled statistic downstream.
    """
    out: dict[str, float] = {}
    for key in NUMERIC_TARGETS:
        val = getattr(dset, key, None)
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        if not math.isfinite(val):
            continue
        out[key] = float(val)
    return out


@dataclass
class DatasetRow:
    row_id: str
    fingerprint: SessionFingerprint
    targets: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Baselines. Each `fit` returns a predict(row) -> {target: value} callable.
# ---------------------------------------------------------------------------

def fit_mean(train: list[DatasetRow]) -> Callable[[DatasetRow], dict[str, float]]:
    """Predict the per-target training mean, ignoring the query. The baseline
    every structural model must beat to have earned its keep."""
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for r in train:
        for k, v in r.targets.items():
            sums[k] = sums.get(k, 0.0) + v
            counts[k] = counts.get(k, 0) + 1
    means = {k: sums[k] / counts[k] for k in sums}
    return lambda _row: dict(means)


def fit_nearest_fingerprint(
    train: list[DatasetRow],
) -> Callable[[DatasetRow], dict[str, float]]:
    """Predict the targets of the structurally most-similar training session
    (reusing the fingerprint similarity). Interpretable and honest for small N."""
    def predict(row: DatasetRow) -> dict[str, float]:
        if not train:
            return {}
        best = max(train, key=lambda tr: similarity(row.fingerprint, tr.fingerprint))
        return dict(best.targets)
    return predict


ModelFactory = Callable[[list[DatasetRow]], Callable[[DatasetRow], dict[str, float]]]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_loo(rows: list[DatasetRow], fit: ModelFactory) -> dict:
    """Leave-one-out MAE **per target** (scale-safe), plus per-fold coverage.

    Returns {"n": N, "per_target": {t: mae}, "folds_scored": {t: k},
    "n_predictions": k}. Only targets present in BOTH the held-out truth and the
    prediction are scored. We deliberately do NOT return a single pooled MAE:
    pooling absolute errors across targets on incommensurable scales (Hz vs 0..1
    vs LUFS) is dominated by the largest-unit target and can invert the honest
    per-target conclusion. Compare models via ``skill_vs_baseline`` instead.
    """
    abs_err: dict[str, list[float]] = {}
    n_pred = 0
    for i, held in enumerate(rows):
        train = rows[:i] + rows[i + 1:]
        if not train:
            continue
        pred = fit(train)(held)
        for k, truth in held.targets.items():
            if k in pred:
                abs_err.setdefault(k, []).append(abs(pred[k] - truth))
                n_pred += 1
    per_target = {k: sum(v) / len(v) for k, v in abs_err.items() if v}
    return {
        "n": len(rows),
        "per_target": per_target,
        "folds_scored": {k: len(v) for k, v in abs_err.items()},
        "n_predictions": n_pred,
    }


def skill_vs_baseline(model: dict, baseline: dict) -> dict:
    """Per-target skill = 1 - MAE_model / MAE_baseline (>0 => beats the mean).

    Normalized per target so descriptors on wildly different scales (LUFS vs a
    0..1 RMS) are comparable. The headline aggregate is the **median** skill plus
    a win count, NOT the arithmetic mean: skill is bounded above by 1 but
    unbounded below, so one badly-missed target would drag a mean sharply
    negative and flip the "structure helps" conclusion even when the model beats
    the baseline on most targets. Median and win-count are robust to that.
    """
    out: dict[str, float] = {}
    for k, base_mae in baseline.get("per_target", {}).items():
        m = model.get("per_target", {}).get(k)
        if m is None or base_mae is None:
            continue
        if base_mae == 0.0:
            out[k] = 0.0 if m == 0.0 else float("-inf")
        else:
            out[k] = 1.0 - (m / base_mae)
    finite = [v for v in out.values() if math.isfinite(v)]
    n = len(out)
    return {
        "per_target": out,
        "median_skill": median(finite) if finite else None,
        "mean_skill": (sum(finite) / len(finite)) if finite else None,
        "n_targets": n,
        "n_beats_mean": sum(1 for v in out.values() if v > 0.0),
    }


# ---------------------------------------------------------------------------
# Intervention effects — captures the within-A/B parameter changes that a
# structural fingerprint cannot see.
# ---------------------------------------------------------------------------

def intervention_effect(intervention_type: str,
                        before: AudioDescriptorSet,
                        after: AudioDescriptorSet) -> dict:
    """Measured acoustic delta for one controlled A/B, tagged by intervention."""
    ta, tb = descriptor_targets(before), descriptor_targets(after)
    deltas = {k: round(tb[k] - ta[k], 4) for k in ta if k in tb}
    directions = {k: (1 if d > 0 else -1 if d < 0 else 0) for k, d in deltas.items()}
    return {
        "intervention_type": intervention_type,
        "deltas": deltas,
        "directions": directions,
    }


@dataclass
class PredictionReport:
    n_samples: int
    targets: list[str]
    mean_baseline: dict
    nearest_model: dict
    skill: dict
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        n_targets = self.skill.get("n_targets", 0)
        n_beats = self.skill.get("n_beats_mean", 0)
        mean_mae = self.mean_baseline.get("per_target", {})
        nn_mae = self.nearest_model.get("per_target", {})
        lines = [
            "=" * 64,
            "STATE -> ACOUSTIC PREDICTION (baseline evaluation)",
            "=" * 64,
            f"Samples: {self.n_samples}   Targets scored: {n_targets}",
            "*** SCAFFOLD: synthetic fixtures, tiny N — this validates the task "
            "and harness, NOT a predictive claim. ***",
            "",
            "Per-target LOO MAE  (mean-baseline  ->  nearest-fingerprint):",
        ]
        per = self.skill.get("per_target", {})
        for k in sorted(per, key=lambda x: (math.isfinite(per[x]), per[x]),
                        reverse=True):
            lines.append(f"    {k:26s} {_fmt(mean_mae.get(k))} -> "
                         f"{_fmt(nn_mae.get(k))}   skill {_fmt(per[k])}")
        lines += [
            "",
            "Aggregate skill vs mean baseline (per-target; >0 = structure helps):",
            f"    beats mean on:   {n_beats}/{n_targets} targets",
            f"    median skill:    {_fmt(self.skill.get('median_skill'))}  "
            "(robust headline)",
            f"    mean skill:      {_fmt(self.skill.get('mean_skill'))}  "
            "(fragile: unbounded below)",
        ]
        if self.notes:
            lines.append("")
            lines.append("Notes:")
            lines.extend(f"  - {n}" for n in self.notes)
        return "\n".join(lines)


def _fmt(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    if not math.isfinite(x):
        return "nan" if math.isnan(x) else ("+inf" if x > 0 else "-inf")
    return f"{x:+.4f}" if x < 0 else f"{x:.4f}"


def evaluate_dataset(rows: list[DatasetRow]) -> PredictionReport:
    mean = evaluate_loo(rows, fit_mean)
    nn = evaluate_loo(rows, fit_nearest_fingerprint)
    skill = skill_vs_baseline(nn, mean)
    targets = sorted({k for r in rows for k in r.targets})
    notes = [
        "Nearest-fingerprint uses structural similarity only. Within an A/B pair "
        "that differs by a single plug-in PARAMETER, the fingerprints are "
        "identical, so structure cannot explain that acoustic delta — see the "
        "intervention-effects view. This is a finding, not a bug: parameter-level "
        "state must enter the representation to close that gap.",
        "DEGENERACY DISCLOSURE: on a 2-families-of-2 fixture set, every LOO "
        "nearest neighbour is the held-out session's A/B partner (identical "
        "fingerprint => similarity 1.0). Any positive skill here therefore only "
        "restates 'the two renders in a pair are closer to each other than to "
        "the other family' — it demonstrates the HARNESS, not structural "
        "generalization. A real corpus with many independent sessions is needed "
        "before any skill number is evidence of a predictive signal.",
    ]
    return PredictionReport(
        n_samples=len(rows), targets=targets,
        mean_baseline=mean, nearest_model=nn, skill=skill, notes=notes,
    )


# ---------------------------------------------------------------------------
# CLI (entry point: state-audio-eval) — builds a dataset from fixture renders.
# ---------------------------------------------------------------------------

def _build_fixture_rows(fixtures_dir: str) -> list[DatasetRow]:
    import os

    from .audio_descriptors import extract as extract_audio
    from .fingerprint import fingerprint
    from .fusion import ingest

    rows: list[DatasetRow] = []
    for stem in ("dualfilter_a", "dualfilter_b", "routing_a", "routing_b"):
        dp = os.path.join(fixtures_dir, f"{stem}.dawproject")
        wav = os.path.join(fixtures_dir, f"{stem}.wav")
        if not (os.path.exists(dp) and os.path.exists(wav)):
            continue
        fp = fingerprint(ingest(dp).session)
        fp.session_id = stem
        targets = descriptor_targets(extract_audio(wav, source_id=stem))
        if targets:
            rows.append(DatasetRow(row_id=stem, fingerprint=fp, targets=targets))
    return rows


def _cli(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="state-audio-eval",
        description="Evaluate state->acoustic baselines (scaffold on fixtures)")
    parser.add_argument("--fixtures", default="fixtures/cubase")
    args = parser.parse_args(argv)

    rows = _build_fixture_rows(args.fixtures)
    if len(rows) < 2:
        print("Need >=2 rendered fixtures (run tools/make_fixtures.py first).")
        return 1
    print(evaluate_dataset(rows).render())
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
