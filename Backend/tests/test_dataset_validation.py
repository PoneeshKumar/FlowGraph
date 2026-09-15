from app.services import dataset_validation as dv


def _write(tmp_path, text):
    p = tmp_path / "t.csv"; p.write_text(text); return p


def test_template_has_header_and_one_parseable_row(tmp_path):
    p = _write(tmp_path, dv.template_csv())
    out = dv.validate_transactions_csv(p)
    assert out["ok"] and out["valid_rows"] == 1


def test_rejects_wrong_layout_and_empty(tmp_path):
    bad = _write(tmp_path, "a,b,c\n1,2,3\n")
    out = dv.validate_transactions_csv(bad)
    assert not out["ok"] and "Timestamp" in out["error"]
    empty = _write(tmp_path, "")
    assert not dv.validate_transactions_csv(empty)["ok"]


def test_accepts_when_most_sample_rows_parse(tmp_path):
    good = dv.template_csv().splitlines()[1]
    p = _write(tmp_path, ",".join(dv.CSV_COLUMNS) + "\n" + good + "\n" + "garbage\n" + good + "\n")
    out = dv.validate_transactions_csv(p)
    assert out["ok"] and out["valid_rows"] == 2 and out["rows_checked"] == 3
