"""Pure pieces of the snapshot exporter: featured selection, bundle writing, budget."""
import json
import pytest

from scripts import export_snapshot as ex


def test_choose_featured_diversifies_over_typology_then_fills_by_score():
    rows = [{"account_id": f"a{i}", "combined_score": 1 - i / 100} for i in range(12)]
    typ = {"a0": "CYCLE", "a1": "CYCLE", "a2": "FAN-IN", "a3": "FAN-IN", "a4": "STACK"}
    out = ex.choose_featured(rows, typ.get, n=6)
    assert out[:3] == ["a0", "a2", "a4"]          # one per typology first, best score each
    assert len(out) == 6 and len(set(out)) == 6   # then filled by score, no duplicates
    assert ex.choose_featured(rows, lambda _i: None, n=3) == ["a0", "a1", "a2"]   # unlabelled → by score


def test_write_bundle_and_budget(tmp_path):
    bundle = {"meta.json": {"a": 1}, "graph/x.json": {"nodes": [], "edges": []}}
    sizes = ex.write_bundle(bundle, tmp_path)
    assert (tmp_path / "graph" / "x.json").exists()
    assert json.loads((tmp_path / "meta.json").read_text()) == {"a": 1}
    assert set(sizes) == set(bundle) and all(v > 0 for v in sizes.values())
    ex.budget_check(sizes, limit_bytes=10_000)                      # fine
    with pytest.raises(SystemExit):
        ex.budget_check(sizes, limit_bytes=10)                       # over budget


def test_pick_pairs_prefers_two_hop_targets_and_skips_missing():
    sub = {"edges": [{"data": {"source": "a", "target": "b"}}, {"data": {"source": "b", "target": "c"}},
                     {"data": {"source": "a", "target": "d"}}]}
    assert ex.pick_pairs(["a"], {"a": sub}, n=5) == [("a", "c")]          # c is 2 hops away
    one = {"edges": [{"data": {"source": "x", "target": "y"}}]}
    assert ex.pick_pairs(["x", "zz"], {"x": one}, n=5) == [("x", "y")]   # no subgraph for zz → skipped
    assert ex.pick_pairs(["a", "x"], {"a": sub, "x": one}, n=1) == [("a", "c")]
