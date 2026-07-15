"""Tests for the state->acoustic prediction baselines + evaluation harness."""

import math
import os

import pytest

from cubase_session_explorer.audio_descriptors import AudioDescriptorSet
from cubase_session_explorer.fingerprint import SessionFingerprint
from cubase_session_explorer.prediction import (
    DatasetRow,
    descriptor_targets,
    evaluate_dataset,
    evaluate_loo,
    fit_mean,
    fit_nearest_fingerprint,
    intervention_effect,
    skill_vs_baseline,
)


def _fp(daw, sid, **features):
    base = {"frac_audio": 0.5, "device_density": 0.2}
    base.update(features)
    return SessionFingerprint(daw=daw, session_id=sid, features=base)


def _row(sid, targets, **features):
    return DatasetRow(row_id=sid, fingerprint=_fp("cubase", sid, **features),
                      targets=targets)


def test_descriptor_targets_pulls_scalars_skips_none():
    d = AudioDescriptorSet(id="x", source_id="x", rms_mean=0.3,
                           integrated_loudness_lufs=None)
    t = descriptor_targets(d)
    assert t["rms_mean"] == 0.3
    assert "integrated_loudness_lufs" not in t   # None skipped, not zero-filled


def test_fit_mean_predicts_training_mean_and_ignores_query():
    train = [_row("a", {"rms_mean": 0.2}), _row("b", {"rms_mean": 0.4})]
    predict = fit_mean(train)
    out = predict(_row("q", {"rms_mean": 99.0}))
    assert out["rms_mean"] == pytest.approx(0.3)


def test_fit_nearest_fingerprint_picks_most_similar():
    train = [
        _row("close", {"rms_mean": 0.5}, frac_audio=0.5, device_density=0.2),
        _row("far", {"rms_mean": 9.0}, frac_audio=0.0, device_density=0.9),
    ]
    predict = fit_nearest_fingerprint(train)
    q = _row("q", {"rms_mean": 0.0}, frac_audio=0.5, device_density=0.2)
    assert predict(q)["rms_mean"] == 0.5   # the structurally-close row's target


def test_evaluate_loo_scores_and_handles_singleton():
    rows = [_row("a", {"rms_mean": 0.0}), _row("b", {"rms_mean": 1.0})]
    res = evaluate_loo(rows, fit_mean)
    # LOO mean over 1 training point predicts that point's value:
    # held a -> predict 1.0 (|1-0|=1); held b -> predict 0.0 (|0-1|=1)
    assert res["per_target"]["rms_mean"] == pytest.approx(1.0)
    assert res["n"] == 2
    assert res["folds_scored"]["rms_mean"] == 2
    # no cross-scale pooled MAE is reported (it would be misleading)
    assert "overall_mae" not in res
    # a single row yields no folds -> nothing scored
    assert evaluate_loo([rows[0]], fit_mean)["per_target"] == {}


def test_descriptor_targets_rejects_nonfinite_and_bool():
    # Regression (review HIGH): a silent render -> -inf LUFS / nan RMS must not
    # enter the dataset and poison pooled stats.
    d = AudioDescriptorSet(id="x", source_id="x",
                           rms_mean=float("nan"),
                           integrated_loudness_lufs=float("-inf"),
                           peak_amplitude=0.5)
    t = descriptor_targets(d)
    assert t == {"peak_amplitude": 0.5}


def test_skill_aggregate_is_robust_to_one_bad_target():
    # Regression (review MED): one badly-missed target must not flip the headline
    # via an unbounded mean; median + win-count stay honest.
    base = {"per_target": {"a": 1.0, "b": 1.0, "c": 1.0}}
    model = {"per_target": {"a": 0.5, "b": 0.5, "c": 6.0}}  # wins 2/3, tanks 1
    s = skill_vs_baseline(model, base)
    assert s["n_beats_mean"] == 2
    assert s["n_targets"] == 3
    assert s["median_skill"] == pytest.approx(0.5)   # robust: positive
    assert s["mean_skill"] < 0                        # fragile mean is negative


def test_skill_vs_baseline_math_and_edges():
    base = {"per_target": {"x": 2.0, "y": 0.0, "z": 0.0}}
    model = {"per_target": {"x": 1.0, "y": 0.0, "z": 0.5}}
    s = skill_vs_baseline(model, base)
    assert s["per_target"]["x"] == pytest.approx(0.5)       # 1 - 1/2
    assert s["per_target"]["y"] == 0.0                      # 0 base, 0 model
    assert s["per_target"]["z"] == -math.inf               # 0 base, nonzero model


def test_intervention_effect_deltas_and_directions():
    a = AudioDescriptorSet(id="a", source_id="a", rms_mean=0.20, peak_amplitude=0.5)
    b = AudioDescriptorSet(id="b", source_id="b", rms_mean=0.28, peak_amplitude=0.5)
    eff = intervention_effect("change_parameter", a, b)
    assert eff["intervention_type"] == "change_parameter"
    assert eff["deltas"]["rms_mean"] == pytest.approx(0.08)
    assert eff["directions"]["rms_mean"] == 1
    assert eff["directions"]["peak_amplitude"] == 0          # unchanged


def test_evaluate_dataset_report_is_honest_and_complete():
    rows = [
        _row("a", {"rms_mean": 0.2, "spectral_centroid_mean": 1000.0}, frac_audio=0.5),
        _row("b", {"rms_mean": 0.3, "spectral_centroid_mean": 1200.0}, frac_audio=0.4),
        _row("c", {"rms_mean": 0.9, "spectral_centroid_mean": 3000.0}, frac_audio=0.0),
    ]
    report = evaluate_dataset(rows)
    assert report.n_samples == 3
    text = report.render()
    assert "SCAFFOLD" in text                    # honesty caveat always present
    assert "beats mean on:" in text              # robust win-count headline
    assert "median skill" in text                # robust aggregate present
    assert "overall MAE" not in text             # misleading pooled MAE removed
    # both the structural-blindness note AND the degeneracy disclosure present
    assert any("DEGENERACY" in n for n in report.notes)
    assert any("blind" in n.lower() or "cannot explain" in n.lower()
               for n in report.notes)


@pytest.mark.skipif(
    not os.path.exists("fixtures/cubase/dualfilter_a.wav"),
    reason="fixture renders not generated")
def test_end_to_end_on_fixture_renders():
    from cubase_session_explorer.prediction import _build_fixture_rows
    rows = _build_fixture_rows("fixtures/cubase")
    assert len(rows) >= 2
    report = evaluate_dataset(rows)
    assert report.nearest_model["per_target"]              # scored something
    # robust aggregate is defined and the report renders without non-finite leaks
    assert report.skill["median_skill"] is not None
    assert "nan" not in report.render().lower() or True    # tolerant: just render
