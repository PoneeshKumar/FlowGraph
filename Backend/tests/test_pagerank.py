import pytest

from algorithms.pagerank import compute_weighted_pagerank


@pytest.mark.parametrize("adjacency", [{"A": {"B": 2.0}, "B": {"C": 1.0}}, {"A": {"B": 2.0}}])
def test_compute_weighted_pagerank_returns_scores(adjacency):
    scores = compute_weighted_pagerank(adjacency)

    assert set(scores) >= {"A", "B", "C"} if adjacency != {"A": {"B": 2.0}} else set(scores) >= {"A", "B"}
    assert scores["A"] >= 0.0
    assert scores["B"] >= 0.0


def test_compute_weighted_pagerank_accepts_pandas_mapping():
    pd = pytest.importorskip("pandas")
    adjacency = pd.Series(
        [{"B": 2.0}, {"C": 1.0}],
        index=["A", "B"],
    )

    scores = compute_weighted_pagerank(adjacency)

    assert set(scores) == {"A", "B", "C"}
    assert scores["A"] >= 0.0
    assert scores["B"] >= 0.0


def test_compute_weighted_pagerank_rejects_invalid_input():
    with pytest.raises(TypeError):
        compute_weighted_pagerank(["A", "B"])
