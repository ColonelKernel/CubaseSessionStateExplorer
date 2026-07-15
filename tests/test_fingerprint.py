"""Tests for structural fingerprints + cross-DAW similarity / retrieval."""

import json
import os

from cubase_session_explorer.fingerprint import (
    FEATURE_KEYS,
    SessionFingerprint,
    bag_jaccard,
    cosine,
    feature_deltas,
    fingerprint,
    load_corpus,
    retrieve_similar,
    similarity,
)
from cubase_session_explorer.fusion import ingest


def _fp(fixtures_dir, name):
    return fingerprint(ingest(os.path.join(fixtures_dir, name)).session)


def test_fingerprint_counts_and_vector_shape(fixtures_dir):
    fp = _fp(fixtures_dir, "demo_session.dawproject")
    assert fp.daw == "cubase"
    assert fp.counts["tracks"] == 4
    assert fp.counts["fx_channels"] == 1
    assert fp.counts["groups"] == 1
    assert fp.counts["has_master"] == 1
    # vector is stable-length and in [0,1]
    v = fp.vector()
    assert len(v) == len(FEATURE_KEYS)
    assert all(0.0 <= x <= 1.0 for x in v)
    # fractions of the counted lanes don't exceed 1
    assert fp.features["frac_audio"] <= 1.0


def test_self_similarity_is_one_and_symmetric(fixtures_dir):
    a = _fp(fixtures_dir, "demo_session.dawproject")
    b = _fp(fixtures_dir, "routing_b.dawproject")
    assert similarity(a, a) == 1.0
    assert abs(similarity(a, b) - similarity(b, a)) < 1e-9
    assert 0.0 <= similarity(a, b) <= 1.0


def test_cosine_and_jaccard_edge_cases():
    assert cosine([0, 0, 0], [0, 0, 0]) == 1.0        # both empty -> identical
    assert cosine([0, 0], [1, 1]) == 0.0              # one empty -> orthogonal
    assert abs(cosine([1, 0], [1, 0]) - 1.0) < 1e-9
    assert bag_jaccard({}, {}) == 1.0
    assert bag_jaccard({"EQ": 2}, {"EQ": 2}) == 1.0
    assert bag_jaccard({"EQ": 1}, {"Dynamics": 1}) == 0.0
    assert bag_jaccard({"EQ": 2, "Dynamics": 1}, {"EQ": 1}) == 1 / 3


def test_retrieve_excludes_query_and_ranks(fixtures_dir):
    query = _fp(fixtures_dir, "demo_session.dawproject")
    corpus = [
        _fp(fixtures_dir, "demo_session.dawproject"),  # same id+daw -> excluded
        _fp(fixtures_dir, "routing_b.dawproject"),
        _fp(fixtures_dir, "dualfilter_a.dawproject"),
    ]
    results = retrieve_similar(query, corpus, k=5)
    ids = [r["session_id"] for r in results]
    assert "demo_session" not in ids                  # query itself excluded
    # sorted descending by similarity
    sims = [r["similarity"] for r in results]
    assert sims == sorted(sims, reverse=True)
    assert all("top_differences" in r for r in results)


def test_cross_daw_retrieval_from_precomputed_fingerprint(fixtures_dir):
    query = _fp(fixtures_dir, "demo_session.dawproject")
    # a foreign-DAW session enters the corpus as a precomputed fingerprint,
    # with NO Cubase parser involved — an identical-feature reaper twin.
    reaper = SessionFingerprint(
        daw="reaper", session_id="reaper_twin",
        features=dict(query.features),
        device_families=dict(query.device_families),
        track_types=dict(query.track_types), counts=dict(query.counts))
    other = _fp(fixtures_dir, "dualfilter_a.dawproject")
    results = retrieve_similar(query, [other, reaper], k=5)
    assert results[0]["session_id"] == "reaper_twin"
    assert results[0]["daw"] == "reaper"
    assert results[0]["similarity"] == 1.0
    # same-daw-only filter drops the reaper twin
    same = retrieve_similar(query, [other, reaper], k=5, cross_daw=False)
    assert all(r["daw"] == "cubase" for r in same)


def test_feature_deltas_orders_by_magnitude(fixtures_dir):
    a = _fp(fixtures_dir, "demo_session.dawproject")
    b = _fp(fixtures_dir, "dualfilter_a.dawproject")
    deltas = feature_deltas(a, b, top=4)
    assert len(deltas) == 4
    mags = [d["abs_delta"] for d in deltas]
    assert mags == sorted(mags, reverse=True)


def test_load_corpus_from_jsonl_with_foreign_fingerprint(fixtures_dir, tmp_path):
    query = _fp(fixtures_dir, "demo_session.dawproject")
    row = {"observation_id": "obs-x", "daw": "ableton",
           "state_snapshot": "n/a",
           "fingerprint": SessionFingerprint(
               daw="ableton", session_id="live_set",
               features=dict(query.features)).to_dict()}
    jsonl = tmp_path / "observations.jsonl"
    jsonl.write_text(json.dumps(row) + "\n")
    corpus = load_corpus([str(jsonl)])
    assert len(corpus) == 1
    assert corpus[0].daw == "ableton"
    assert corpus[0].session_id == "live_set"


def test_fingerprint_handles_none_coverage_and_empty_session():
    # Regression (review HIGH): coverage_percent is None by default for any
    # session not built through fusion; must not crash.
    from cubase_session_explorer.models import ProjectMeta, SessionState
    s = SessionState(project=ProjectMeta(project_name="bare"))
    assert s.capture.coverage_percent is None
    fp = fingerprint(s)  # must not raise
    assert fp.features["observability"] == 0.0
    assert all(0.0 <= v <= 1.0 for v in fp.vector())


def test_features_stay_in_unit_range_with_many_folders_and_master_chain():
    # Regression (review MED): frac_folder disjoint numerator, and master-chain
    # devices vs a master-excluding denominator, both broke [0,1] / scale-inv.
    from cubase_session_explorer.models import (
        DeviceState, FolderState, ProjectMeta, SessionState, TrackState,
    )
    s = SessionState(project=ProjectMeta(project_name="x"))
    s.tracks = [TrackState(id="t1", index=0, name="A", track_type="audio")]
    s.folders = [FolderState(id=f"f{i}", name=f"F{i}", index=i) for i in range(5)]
    s.master_track = TrackState(
        id="m", index=99, name="Master", track_type="master",
        devices=[DeviceState(id=f"md{i}", track_id="m", index=i, name=f"Mb{i}")
                 for i in range(8)])
    fp = fingerprint(s)
    assert fp.features["frac_folder"] <= 1.0
    # master-only inserts do NOT inflate device_density (numerator excludes master)
    assert fp.features["device_density"] == 0.0
    assert all(0.0 <= v <= 1.0 for v in fp.vector())


def test_same_named_distinct_sessions_not_wrongly_excluded(fixtures_dir):
    # Regression (review MED): session_id collision in self-exclusion.
    query = _fp(fixtures_dir, "demo_session.dawproject")
    twin_name = _fp(fixtures_dir, "routing_b.dawproject")
    twin_name.session_id = "demo_session"   # same name, DIFFERENT features
    twin_name.daw = "cubase"
    results = retrieve_similar(query, [twin_name], k=5)
    assert len(results) == 1                # not dropped as "the query itself"
    # but the genuine query entry IS excluded
    exact = _fp(fixtures_dir, "demo_session.dawproject")
    assert retrieve_similar(query, [exact], k=5) == []


def test_fingerprint_roundtrips_through_dict(fixtures_dir):
    fp = _fp(fixtures_dir, "folder_group.dawproject")
    fp2 = SessionFingerprint.from_dict(fp.to_dict())
    assert fp2.features == fp.features
    assert similarity(fp, fp2) == 1.0
