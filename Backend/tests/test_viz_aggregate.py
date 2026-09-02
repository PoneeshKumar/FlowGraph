from app.viz.aggregate import aggregate_account, MarkWeights, MarkThresholds

W = MarkWeights()
T = MarkThresholds()


def test_unmarked_returns_none():
    assert aggregate_account("a", gnn_score=0.1, in_cycle=False,
                             community_tier="low", weights=W, thresholds=T) is None


def test_gnn_signal_marks_and_scores():
    out = aggregate_account("a", gnn_score=0.92, in_cycle=False,
                            community_tier="low", weights=W, thresholds=T)
    assert out is not None
    assert out["signals"] == {"gnn": True, "cycle": False, "community": False}
    # only the gnn signal is present, so combined == its own score
    assert out["combined_score"] == 0.92
    assert "gnn" in out["rationale"].lower()


def test_cycle_signal_marks_with_no_gnn():
    out = aggregate_account("a", gnn_score=None, in_cycle=True,
                            community_tier=None, weights=W, thresholds=T)
    assert out["signals"] == {"gnn": False, "cycle": True, "community": False}
    assert out["combined_score"] == 1.0  # cycle signal contributes 1.0


def test_multiple_signals_blend_and_renormalize():
    out = aggregate_account("a", gnn_score=0.8, in_cycle=True,
                            community_tier="critical", weights=W, thresholds=T)
    assert out["signals"] == {"gnn": True, "cycle": True, "community": True}
    # weighted mean over present signals: (.6*.8 + .25*1 + .15*1) / (.6+.25+.15)
    assert abs(out["combined_score"] - (0.6 * 0.8 + 0.25 * 1 + 0.15 * 1)) < 1e-9


def test_community_tier_below_threshold_is_not_a_signal():
    out = aggregate_account("a", gnn_score=0.92, in_cycle=False,
                            community_tier="medium", weights=W, thresholds=T)
    assert out["signals"]["community"] is False
