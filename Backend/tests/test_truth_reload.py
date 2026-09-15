"""truth.reload swaps the ground-truth source: a patterns file, or None → empty."""
from pathlib import Path
from app.viz import truth


def test_reload_none_clears_labels():
    truth.reload(None)
    assert truth.truth_set() == set() and truth.typology_of("anything") is None


def test_reload_default_restores_ibm_labels_when_file_present():
    n = truth.reload(truth.DEFAULT)
    if not Path("benchmarks/data/HI-Small_Patterns.txt").exists():
        assert n == 0
    else:
        assert n > 3000 and truth.typology_of(next(iter(truth.truth_set()))) is not None
    truth.reload(truth.DEFAULT)   # leave the module as the app expects it
