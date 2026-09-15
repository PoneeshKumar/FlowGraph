"""Pre-flight checks for an uploaded transaction CSV (spec §6.10).

The parser is the ingestor's own (`_row_from_parts` / `_row_to_transfer`), so
"valid" here means exactly "the ingest would write this row".
"""
import csv
from pathlib import Path
from typing import Any, Dict

from benchmarks.ibm_aml.patterns import _CSV_COLS, _row_from_parts
from benchmarks.ibm_aml.ingestor import _row_to_transfer

CSV_COLUMNS = list(_CSV_COLS)

_EXAMPLE_ROW = ["2022/09/01 00:20", "010", "8000EBD30", "010", "8000ECA90",
                "3697.34", "US Dollar", "3697.34", "US Dollar", "Reinvestment", "0"]


def template_csv() -> str:
    """Header + one example row in the exact positional layout the ingest reads."""
    return ",".join(CSV_COLUMNS) + "\n" + ",".join(_EXAMPLE_ROW) + "\n"


def validate_transactions_csv(path: Path, sample: int = 5) -> Dict[str, Any]:
    """Parse the header and the first `sample` data rows. OK when at least one
    row parses and at least half of the checked rows do."""
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if not header:
                return {"ok": False, "rows_checked": 0, "valid_rows": 0, "error": "The file is empty."}
            checked = valid = 0
            for parts in reader:
                if checked >= sample:
                    break
                checked += 1
                if _row_to_transfer(_row_from_parts(parts)) is not None:
                    valid += 1
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return {"ok": False, "rows_checked": 0, "valid_rows": 0, "error": f"Could not read the file: {exc}"}
    ok = valid >= 1 and valid * 2 >= checked
    error = None if ok else (
        "No parseable rows in the first %d. Expected the columns, in order: %s"
        % (checked, ", ".join(CSV_COLUMNS)))
    return {"ok": ok, "rows_checked": checked, "valid_rows": valid, "error": error}
