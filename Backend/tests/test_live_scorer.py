"""LiveScorer: cached-model inductive scoring reproduces the ensemble path.

Skips if the champion checkpoint or torch is unavailable (ml/runs is gitignored),
so this is safe in a bare CI environment and exercised locally where v10_L3 lives.
"""
import json
from pathlib import Path

import numpy as np
import pytest

_RUN = Path("ml/runs/v10_L3")


def _requires_champion():
    if not (_RUN / "model.pt").exists() or not (_RUN / "result.json").exists():
        pytest.skip("champion checkpoint ml/runs/v10_L3 not present")
    try:
        import torch  # noqa: F401
    except Exception:
        pytest.skip("torch not installed")


def _synthetic_feature_set(n=4):
    """A tiny graph with the champion's exact 47-column feature layout."""
    from ml.features import FeatureSet
    names = json.loads((_RUN / "result.json").read_text())["feature_names"]
    rng = np.random.default_rng(0)
    x = rng.random((n, len(names)), dtype=np.float64).astype(np.float32)
    edge_index = np.array([[0, 1, 2, 3, 0], [1, 2, 3, 0, 2]], dtype=np.int64)
    return FeatureSet(
        node_ids=[f"n{i}" for i in range(n)], x=x,
        y=np.zeros(n, np.int64), labelled_mask=np.zeros(n, bool),
        edge_index=edge_index, edge_weight=np.ones(edge_index.shape[1], np.float32),
        feature_names=list(names),
    )


def test_live_scorer_outputs_valid_probabilities():
    _requires_champion()
    from ml.live_scorer import LiveScorer
    fs = _synthetic_feature_set()
    scores = LiveScorer(str(_RUN)).score(fs)
    assert scores.shape == (fs.num_nodes,)
    assert np.all(scores >= 0.0) and np.all(scores <= 1.0)


def test_live_scorer_matches_ensemble_scores():
    _requires_champion()
    from ml.live_scorer import LiveScorer
    from ml.ensemble import ensemble_scores
    fs = _synthetic_feature_set()
    cached = LiveScorer(str(_RUN)).score(fs)          # resident model
    reload_ = np.asarray(ensemble_scores([_RUN], fs))  # reloads from disk each call
    assert np.allclose(cached, reload_, atol=1e-6)     # same weights, same scaler


def test_live_scorer_rejects_wrong_feature_layout():
    _requires_champion()
    from ml.live_scorer import LiveScorer
    fs = _synthetic_feature_set()
    fs.feature_names = fs.feature_names[::-1]           # scramble the column contract
    with pytest.raises(ValueError):
        LiveScorer(str(_RUN)).score(fs)
