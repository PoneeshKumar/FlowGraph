# "Analyze a File" Upload (Plan C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a self-hoster upload a transaction CSV (optionally with a patterns file), replace the loaded graph with it, and run the whole detection pipeline — from the React app, with staged progress.

**Architecture:** `POST /api/datasets/upload` validates the file *before* touching any store, then launches one background job recorded in the existing `pipeline_runs` table with stages `reset → ingest → pagerank → louvain → cycle → gnn → aggregate`. `DatasetRunner` wires the ingest path (`reset_stores`, `ingest_for_training`) to the existing `PipelineRunner`, which gains a `feature_source="live"` mode that rebuilds GNN features from the stores instead of loading the IBM `.npz` cache. The frontend polls the run and hides label-dependent metrics when the dataset is unlabelled.

**Tech Stack:** FastAPI `UploadFile` (needs `python-multipart`), the existing ingest/feature/pipeline modules, React + the Plan B data-source layer.

**Spec:** `Backend/docs/superpowers/specs/2026-09-15-showcase-and-ship-design.md` §6.10, §8.3 (Upload), §10. Plans A and B are complete on this branch.

## Global Constraints

- Everything in Plan A's Global Constraints still applies (Python 3.9, pytest invocation, commit trailer, `POSTGRES_DSN` for live checks).
- **Never run a real upload against the dev stack during this plan.** `reset` wipes Neo4j/Redis/`risk_flags`; restoring the IBM demo takes `run_ingest --reset` (~10 min) + `run_louvain` + a pipeline run. Live checks cover only: `GET /datasets/current`, `GET /datasets/template`, and rejected uploads (missing confirm, malformed CSV) — all of which return before any store is touched. The destructive path is unit-tested with fakes.
- Uploaded files live under `Backend/uploads/` (gitignored).

## File structure

| File | Responsibility |
|---|---|
| `ml/datasets/ibm_aml.py` (modify) | public `reset_stores`; `ingest_for_training(patterns_path=None)` |
| `ml/datasets/run_ingest.py` (modify) | use the public `reset_stores` |
| `app/viz/truth.py` (modify) | `reload(patterns_path)` — swap or clear the ground-truth set |
| `db/postgres.py` (modify) | `clear_risk_flags()` |
| `app/core/config.py` (modify) | `UPLOAD_DIR`, `UPLOAD_MAX_MB`, `GNN_FEATURE_CACHE_UPLOAD` |
| `app/viz/runner.py` (modify) | `feature_source="live"` + optional redis client |
| `app/services/dataset_runner.py` (new) | the upload job |
| `app/services/dataset_validation.py` (new) | pure CSV validation + template |
| `app/api/endpoints.py` (modify) | `/datasets/current`, `/datasets/template`, `/datasets/upload` |
| `requirements.txt` (modify) | `python-multipart` |
| `Frontend/src/services/dataSource.js` (modify) | `datasets.*` |
| `Frontend/src/components/UploadView.jsx` (rewrite) | the upload flow |
| tests: `tests/test_dataset_validation.py`, `tests/test_dataset_runner.py`, `tests/test_datasets_api.py`, `tests/test_truth_reload.py`, `Frontend/src/services/dataSource.test.js` (extend) | |

---

### Task 1: Ingest-side primitives — `reset_stores`, unlabelled ingest, `truth.reload`, `clear_risk_flags`

**Files:**
- Modify: `ml/datasets/ibm_aml.py`, `ml/datasets/run_ingest.py`, `app/viz/truth.py`, `db/postgres.py`, `app/core/config.py`
- Test: `tests/test_truth_reload.py`, `tests/test_postgres_meta.py` (append)

**Interfaces:**
- Produces: `ibm_aml.reset_stores(neo4j_client, redis_client, batch_size=20_000)`; `ingest_for_training(csv_path, patterns_path: Optional[...] , ...)` accepts `None`; `truth.reload(patterns_path: Optional[Path]) -> int`; `PostgresClient.clear_risk_flags() -> int`; settings `UPLOAD_DIR="uploads"`, `UPLOAD_MAX_MB=500`, `GNN_FEATURE_CACHE_UPLOAD="ml/cache/featureset_upload.npz"`.

- [ ] **Step 1: Failing tests**

`tests/test_truth_reload.py`:

```python
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
```

Append to `tests/test_postgres_meta.py`:

```python
def test_clear_risk_flags_removes_every_row_and_returns_count():
    async def fn(pg):
        # snapshot + restore so the dev graph's real flags survive this test
        async with pg._get_connection() as conn:
            rows = await conn.fetch("SELECT * FROM risk_flags")
        await pg.upsert_risk_flag(flag_type="TESTTYPE", fingerprint="test:clear", account_ids=["t"],
                                  risk_level="low", risk_score=0.1, explanation="x")
        n = await pg.clear_risk_flags()
        remaining = await pg.count_risk_flags()
        async with pg._get_connection() as conn:
            for r in rows:
                await conn.execute(
                    "INSERT INTO risk_flags (id, flag_type, fingerprint, account_ids, risk_level, risk_score, "
                    "explanation, details, status, first_detected_at, last_detected_at, detection_count, created_at) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) ON CONFLICT (id) DO NOTHING",
                    r["id"], r["flag_type"], r["fingerprint"], r["account_ids"], r["risk_level"], r["risk_score"],
                    r["explanation"], r["details"], r["status"], r["first_detected_at"], r["last_detected_at"],
                    r["detection_count"], r["created_at"])
        return n, remaining, len(rows)
    n, remaining, before = _run(fn)
    assert n == before + 1 and remaining == 0
```

Run: `python3 -m pytest tests/test_truth_reload.py -q -p no:cacheprovider` → FAIL (`no attribute 'reload'`).

- [ ] **Step 2: `reset_stores` public + unlabelled ingest**

In `ml/datasets/ibm_aml.py`, add after `load_pattern_accounts`:

```python
async def reset_stores(neo4j_client: Any, redis_client: Optional[Any], batch_size: int = 20_000) -> None:
    """Wipe the graph so a reload does not inflate FLOWS_TO aggregates.

    FLOWS_TO aggregates are incremented on MATCH, so loading the same rows twice
    doubles tx_count and total_amount. Relationships are deleted BEFORE nodes, in
    two batched phases: DETACH DELETE on a hub with ~14k relationships exceeds
    Neo4j's per-transaction memory limit on the loaded HI-Small graph.
    """
    from config import NEO4J_DATABASE
    logger.warning("reset: deleting all relationships, then all nodes")
    phases = (
        ("relationships", "MATCH ()-[r]->() WITH r LIMIT $batch_size DELETE r RETURN count(*) AS n"),
        ("nodes", "MATCH (n) WITH n LIMIT $batch_size DETACH DELETE n RETURN count(*) AS n"),
    )
    async with neo4j_client.driver.session(database=NEO4J_DATABASE) as session:
        for label, query in phases:
            total = 0
            while True:
                record = await (await session.run(query, batch_size=batch_size)).single()
                deleted = int(record["n"]) if record else 0
                total += deleted
                if not deleted:
                    break
            logger.info("  deleted %d %s", total, label)
    if redis_client is not None:
        await redis_client.client.flushdb()
        logger.info("  redis flushed")
```

Change `ingest_for_training`'s signature line `patterns_path: Union[str, Path],` to `patterns_path: Optional[Union[str, Path]],` and replace

```python
    pattern_accounts, group_count, per_typology = load_pattern_accounts(
        patterns_path, typologies
    )
```

with

```python
    if patterns_path is None:
        pattern_accounts, group_count, per_typology = set(), 0, {}
    else:
        pattern_accounts, group_count, per_typology = load_pattern_accounts(patterns_path, typologies)
```

In `ml/datasets/run_ingest.py` delete the whole `_reset_stores` function and change the call `await _reset_stores(neo4j_client, redis_client)` to `await reset_stores(neo4j_client, redis_client)`, importing it: `from ml.datasets.ibm_aml import ALL_TYPOLOGIES, ingest_for_training, reset_stores`.

- [ ] **Step 3: `truth.reload`**

Replace `app/viz/truth.py`'s module state and `_load` with:

```python
DEFAULT = object()          # sentinel: the IBM patterns file at its default path
_SOURCE: Any = DEFAULT      # DEFAULT | a Path | None (no labels)
_TRUTH: Optional[Set[str]] = None
_TYPOLOGY: Optional[Dict[str, str]] = None


def _load() -> None:
    global _TRUTH, _TYPOLOGY
    if _TRUTH is not None:
        return
    truth: Set[str] = set()
    typ_of: Dict[str, str] = {}
    if _SOURCE is not None:
        try:
            from ml.evaluate import load_ground_truth
            gt = load_ground_truth() if _SOURCE is DEFAULT else load_ground_truth(patterns_path=_SOURCE)
            for typ, accounts in gt.accounts_by_typology.items():
                for acc in accounts:
                    truth.add(acc)
                    typ_of.setdefault(acc, typ)   # an account can appear in several patterns
            logger.info("ground truth loaded: %d dataset-marked accounts", len(truth))
        except Exception as exc:  # noqa: BLE001 — missing/unparseable file → empty labels
            logger.warning("ground truth unavailable (%s); dataset tab will be empty", exc)
    _TRUTH, _TYPOLOGY = truth, typ_of


def reload(patterns_path: Any) -> int:
    """Point the labels at a new patterns file (a Path), the IBM default
    (``DEFAULT``), or nothing (``None``) — used after a dataset upload."""
    global _SOURCE, _TRUTH, _TYPOLOGY
    _SOURCE = patterns_path
    _TRUTH = _TYPOLOGY = None
    return preload()
```

(`from typing import Any, Dict, Optional, Set`.) Keep `preload`, `truth_set`, `typology_of` as they are.

- [ ] **Step 4: `clear_risk_flags` + settings**

In `db/postgres.py` after `get_account_ids_for_flag_type`:

```python
    async def clear_risk_flags(self) -> int:
        """Drop every flag — only for a dataset replacement, where the graph the
        flags describe is gone. Returns the number of rows removed."""
        async with self._get_connection() as conn:
            n = await conn.fetchval("SELECT count(*) FROM risk_flags")
            await conn.execute("DELETE FROM risk_flags")
        return int(n)
```

In `app/core/config.py` after `CYCLE_MAX_SEEDS`:

```python
    # --- "Analyze a file" dataset upload (app/services/dataset_runner.py) ---
    UPLOAD_DIR: str = "uploads"
    UPLOAD_MAX_MB: int = 500
    # Features rebuilt from the stores for an uploaded graph land here and become
    # the runner's cache until the next upload.
    GNN_FEATURE_CACHE_UPLOAD: str = "ml/cache/featureset_upload.npz"
```

- [ ] **Step 5: Verify and commit**

Run: `POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph' python3 -m pytest tests/test_truth_reload.py tests/test_postgres_meta.py tests/test_dataset_ingest.py -q -p no:cacheprovider` → all pass. Full suite green.

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add ml/datasets/ibm_aml.py ml/datasets/run_ingest.py app/viz/truth.py db/postgres.py app/core/config.py tests/test_truth_reload.py tests/test_postgres_meta.py
git commit -m "feat(ingest): public reset_stores, unlabelled ingest, truth.reload, clear_risk_flags" -m "$TRAILER"
```

---

### Task 2: `PipelineRunner` live feature source

**Files:**
- Modify: `app/viz/runner.py`
- Test: `tests/test_viz_runner.py` (append)

**Interfaces:**
- Produces: `PipelineRunner(neo4j, postgres, settings, redis=None, feature_source="cache")`; with `"live"` the GNN stage builds a `FeatureSet` via `FeatureBuilder(neo4j, redis, postgres).build(...)`, saves it to `settings.GNN_FEATURE_CACHE_UPLOAD`, and points `settings.GNN_FEATURE_CACHE` at it.

- [ ] **Step 1: Failing test** — append to `tests/test_viz_runner.py`:

```python
@pytest.mark.asyncio
async def test_gnn_live_feature_source_builds_and_repoints_cache(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.viz.runner import PipelineRunner
    run_dir = tmp_path / "run"; run_dir.mkdir()
    upload_cache = tmp_path / "featureset_upload.npz"
    settings = SimpleNamespace(GNN_RUN_DIR=str(run_dir), GNN_ENSEMBLE_RUNS=[],
                               GNN_FEATURE_CACHE=str(tmp_path / "stale.npz"),
                               GNN_FEATURE_CACHE_UPLOAD=str(upload_cache), CYCLE_MAX_SEEDS=5)
    fs = SimpleNamespace(node_ids=["a", "b"], x=np.zeros((2, 47), dtype=np.float32), y=np.zeros(2, dtype=np.int64),
                         labelled_mask=np.zeros(2, dtype=bool), edge_index=np.zeros((2, 0), dtype=np.int64),
                         edge_weight=np.zeros(0, dtype=np.float32), feature_names=["f"] * 47, node_first_ts=None)
    built = {}
    class FakeBuilder:
        def __init__(self, n, r, p): built["clients"] = (n, r, p)
        async def build(self, **kw): built["kw"] = kw; return fs
    neo4j = MagicMock(get_flows_to_timestamp=AsyncMock(return_value=1663000000), write_gnn_scores=AsyncMock())
    pg = MagicMock(update_pipeline_run=AsyncMock())
    monkeypatch.setattr("app.viz.runner.FeatureBuilder", FakeBuilder)
    monkeypatch.setattr("app.viz.runner.ensemble_scores", lambda dirs, feature_set: np.array([0.2, 0.9]))
    runner = PipelineRunner(neo4j, pg, settings, redis="REDIS", feature_source="live")
    scores = await runner._gnn("rid")
    assert scores == {"a": 0.2, "b": 0.9}
    assert built["clients"] == (neo4j, "REDIS", pg) and built["kw"]["reference_time"] is not None
    assert upload_cache.exists() and settings.GNN_FEATURE_CACHE == str(upload_cache)
```

(`import numpy as np` and `from unittest.mock import AsyncMock, MagicMock` at the top if missing.)

- [ ] **Step 2: Implement** — in `app/viz/runner.py`:

Add imports `from datetime import datetime, timezone` (already), `from ml.features import FeatureBuilder`, `from ml.train import load_feature_cache, save_feature_cache`.

Change the constructor to:

```python
    def __init__(self, neo4j, postgres, settings, redis=None, feature_source: str = "cache"):
        self.neo4j = neo4j
        self.pg = postgres
        self.redis = redis
        self.s = settings
        self.feature_source = feature_source   # "cache" (IBM .npz) | "live" (rebuild from stores)
        self._stage = None
```

Replace the body of `_gnn` from `run_dir = Path(self.s.GNN_RUN_DIR)` through `feature_set = load_feature_cache(cache)` with:

```python
        run_dir = Path(self.s.GNN_RUN_DIR)
        if not run_dir.exists():
            logger.warning("GNN checkpoint missing (%s) — skipping GNN stage", run_dir)
            return {}
        if self.feature_source == "live":
            feature_set = await self._build_live_features()
            upload_cache = Path(self.s.GNN_FEATURE_CACHE_UPLOAD)
            save_feature_cache(feature_set, upload_cache)
            self.s.GNN_FEATURE_CACHE = str(upload_cache)   # later plain runs reuse it
        else:
            cache = Path(self.s.GNN_FEATURE_CACHE)
            if not cache.exists():
                logger.warning("GNN feature cache missing (%s) — skipping GNN stage", cache)
                return {}
            feature_set = load_feature_cache(cache)
```

and add the helper:

```python
    async def _build_live_features(self):
        """FeatureSet from the stores — for an uploaded graph the IBM .npz is wrong.
        Anchored at the 99.9th-percentile edge timestamp so a historical file isn't
        filtered out as stale (same rule as ml/train.build_feature_set)."""
        anchor = await self.neo4j.get_flows_to_timestamp(percentile=0.999)
        reference = datetime.fromtimestamp(anchor, tz=timezone.utc) if anchor else None
        builder = FeatureBuilder(self.neo4j, self.redis, self.pg)
        return await builder.build(window_days=3650, reference_time=reference, export_timeout_seconds=3600.0)
```

- [ ] **Step 3: Verify and commit**

`python3 -m pytest tests/test_viz_runner.py -q -p no:cacheprovider` → all pass (the existing tests still construct `PipelineRunner(neo4j, pg, settings)`).

```bash
git add app/viz/runner.py tests/test_viz_runner.py
git commit -m "feat(pipeline): live feature source for uploaded graphs" -m "$TRAILER"
```

---

### Task 3: Validation + template (pure) and the `DatasetRunner`

**Files:**
- Create: `app/services/dataset_validation.py`, `app/services/dataset_runner.py`
- Test: `tests/test_dataset_validation.py`, `tests/test_dataset_runner.py`

**Interfaces:**
- `dataset_validation.CSV_COLUMNS` (the 11 IBM columns), `template_csv() -> str`, `validate_transactions_csv(path: Path, sample: int = 5) -> Dict` → `{"ok": bool, "rows_checked": int, "valid_rows": int, "error": Optional[str]}`.
- `DatasetRunner(neo4j, redis, pg, settings).run(run_id, csv_path, patterns_path, name)`; stages recorded in `pipeline_runs`.

- [ ] **Step 1: Failing tests**

`tests/test_dataset_validation.py`:

```python
from pathlib import Path
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
```

`tests/test_dataset_runner.py`:

```python
"""DatasetRunner: stage order, meta rewrite, truth reload, cache invalidation — all with fakes."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import dataset_runner as dr


def _run_it(patterns):
    calls = []
    pg = MagicMock(update_pipeline_run=AsyncMock(side_effect=lambda rid, **kw: calls.append(("mark", kw.get("stage"))) or None),
                   clear_risk_flags=AsyncMock(return_value=3), set_app_meta=AsyncMock())
    stats = SimpleNamespace(rows_scanned=10, min_timestamp=datetime(2022, 9, 1, tzinfo=timezone.utc),
                            max_timestamp=datetime(2022, 9, 2, tzinfo=timezone.utc), pattern_accounts=0)
    fake_pipeline = MagicMock(); fake_pipeline.run = AsyncMock(side_effect=lambda rid: calls.append(("pipeline", rid)))
    with patch.object(dr, "reset_stores", AsyncMock(side_effect=lambda n, r: calls.append(("reset", None)))), \
         patch.object(dr, "ingest_for_training", AsyncMock(return_value=stats)) as ingest, \
         patch.object(dr, "PipelineRunner", lambda *a, **k: fake_pipeline), \
         patch.object(dr.truth, "reload", lambda p: calls.append(("truth", p)) or 0), \
         patch.object(dr.stats_service, "invalidate", lambda: calls.append(("inv", "stats"))), \
         patch.object(dr.metrics, "invalidate", lambda: calls.append(("inv", "metrics"))), \
         patch.object(dr.threshold, "invalidate", lambda: calls.append(("inv", "threshold"))):
        runner = dr.DatasetRunner("NEO", "REDIS", pg, SimpleNamespace())
        asyncio.run(runner.run("rid", Path("/tmp/x.csv"), patterns, "my.csv"))
    return calls, pg, ingest


def test_stage_order_and_side_effects_unlabelled():
    calls, pg, ingest = _run_it(None)
    assert calls[:3] == [("mark", "reset"), ("reset", None), ("mark", "ingest")]
    assert ("truth", None) in calls and ("pipeline", "rid") in calls
    assert calls.index(("pipeline", "rid")) > calls.index(("inv", "stats"))
    pg.clear_risk_flags.assert_awaited_once()
    meta = pg.set_app_meta.await_args.args
    assert meta[0] == "dataset" and meta[1]["labelled"] is False and meta[1]["name"] == "my.csv"
    assert meta[1]["start_ts"] == 1661990400 and meta[1]["rows"] == 10 and meta[1]["source"] == "upload"
    assert ingest.await_args.kwargs["recompute_pagerank"] is False and ingest.await_args.kwargs["max_background_rows"] is None


def test_labelled_when_patterns_given():
    calls, pg, _ = _run_it(Path("/tmp/p.txt"))
    assert ("truth", Path("/tmp/p.txt")) in calls and pg.set_app_meta.await_args.args[1]["labelled"] is True


def test_failure_before_pipeline_marks_run_failed():
    pg = MagicMock(update_pipeline_run=AsyncMock(), clear_risk_flags=AsyncMock())
    with patch.object(dr, "reset_stores", AsyncMock(side_effect=RuntimeError("neo4j down"))):
        runner = dr.DatasetRunner("NEO", "REDIS", pg, SimpleNamespace())
        asyncio.run(runner.run("rid", Path("/tmp/x.csv"), None, "x.csv"))
    last = pg.update_pipeline_run.await_args.kwargs
    assert last["status"] == "failed" and last["stage"] == "reset" and "neo4j down" in last["error"]
```

- [ ] **Step 2: Implement validation**

`app/services/dataset_validation.py`:

```python
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
```

- [ ] **Step 3: Implement the runner**

`app/services/dataset_runner.py`:

```python
"""The "Analyze a file" job: replace the loaded graph with an uploaded CSV and run
the whole pipeline on it (spec §6.10). One row in pipeline_runs tracks it; stages
are reset → ingest → pagerank → louvain → cycle → gnn → aggregate.

Destructive by design: `reset` wipes Neo4j, Redis and risk_flags. The API only
launches this after the client confirmed "replace" and the file validated.
"""
import logging
from pathlib import Path
from typing import Any, Optional

from ml.datasets.ibm_aml import ingest_for_training, reset_stores
from app.services import stats_service
from app.viz import metrics, threshold, truth
from app.viz.runner import PipelineRunner

logger = logging.getLogger("dataset_runner")


def _ts(dt: Any) -> Optional[int]:
    return int(dt.timestamp()) if dt is not None else None


class DatasetRunner:
    def __init__(self, neo4j: Any, redis: Any, pg: Any, settings: Any) -> None:
        self.neo4j, self.redis, self.pg, self.s = neo4j, redis, pg, settings
        self._stage: Optional[str] = None

    async def _mark(self, run_id: str, stage: str, progress: float) -> None:
        self._stage = stage
        await self.pg.update_pipeline_run(run_id, status="running", stage=stage, progress=progress)

    async def run(self, run_id: str, csv_path: Path, patterns_path: Optional[Path], name: str) -> None:
        try:
            await self._mark(run_id, "reset", 0.02)
            await reset_stores(self.neo4j, self.redis)
            await self.pg.clear_risk_flags()

            await self._mark(run_id, "ingest", 0.05)
            stats = await ingest_for_training(
                csv_path, patterns_path, self.neo4j, self.redis,
                max_background_rows=None, recompute_pagerank=False)   # the pipeline's own stage does PageRank
            truth.reload(patterns_path)
            await self.pg.set_app_meta("dataset", {
                "name": name, "source": "upload", "labelled": patterns_path is not None,
                "start_ts": _ts(getattr(stats, "min_timestamp", None)),
                "end_ts": _ts(getattr(stats, "max_timestamp", None)),
                "rows": getattr(stats, "rows_scanned", None), "uploaded_at": None,
            })
            stats_service.invalidate()
            metrics.invalidate()
            threshold.invalidate()
            logger.info("dataset %s ingested (%s rows); running the pipeline", name, getattr(stats, "rows_scanned", "?"))
        except Exception as exc:  # noqa: BLE001 — record which stage failed
            logger.exception("dataset run %s failed at %s", run_id, self._stage)
            await self.pg.update_pipeline_run(run_id, status="failed", stage=self._stage, error=str(exc), finished=True)
            return

        runner = PipelineRunner(self.neo4j, self.pg, self.s, redis=self.redis, feature_source="live")
        await runner.run(run_id)          # marks pagerank … aggregate and completed/failed itself
```

- [ ] **Step 4: Verify and commit**

`python3 -m pytest tests/test_dataset_validation.py tests/test_dataset_runner.py -q -p no:cacheprovider` → 6 passed.

```bash
git add app/services/dataset_validation.py app/services/dataset_runner.py tests/test_dataset_validation.py tests/test_dataset_runner.py
git commit -m "feat(datasets): CSV validation/template and the upload job runner" -m "$TRAILER"
```

---

### Task 4: The endpoints

**Files:**
- Modify: `app/api/endpoints.py`, `app/schemas/api.py`, `requirements.txt`
- Test: `tests/test_datasets_api.py`

**Interfaces:**
- `GET /api/datasets/current` → the `app_meta.dataset` row (404 if absent).
- `GET /api/datasets/template` → `text/csv` attachment `flowgraph-template.csv`.
- `POST /api/datasets/upload` multipart: `transactions` (file, required), `patterns` (file, optional), `confirm` (form field, must be `"replace"`) → `{"run_id": "…"}`; 422 without confirm / on a malformed file; 413 over `UPLOAD_MAX_MB`; 409 when a run is active.

- [ ] **Step 1: Failing tests**

`tests/test_datasets_api.py`:

```python
"""Upload endpoints. Every rejection happens before any store is touched, so the
tests never reset anything: the runner is replaced by a fake that records its args."""
import io
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.services.dataset_validation import template_csv


@pytest.fixture
def client(tmp_path):
    from app.viz import deps as viz_deps
    from app.core.config import settings
    with patch.object(viz_deps, "startup", AsyncMock()), patch.object(viz_deps, "shutdown", AsyncMock()), \
         patch.object(settings, "UPLOAD_DIR", str(tmp_path / "uploads")):
        from app.api.main import app
        with TestClient(app) as c:
            yield c


def test_template_and_current(client):
    r = client.get("/api/datasets/template")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0].startswith("Timestamp,From Bank,Account,")
    from app.viz import deps
    with patch.object(deps, "pg", lambda: MagicMock(get_app_meta=AsyncMock(return_value={"name": "X", "source": "upload"}))):
        assert client.get("/api/datasets/current").json()["name"] == "X"
    with patch.object(deps, "pg", lambda: MagicMock(get_app_meta=AsyncMock(return_value=None))):
        assert client.get("/api/datasets/current").status_code == 404


def _files(text):
    return {"transactions": ("t.csv", io.BytesIO(text.encode()), "text/csv")}


def test_upload_requires_confirm_and_valid_csv(client):
    from app.viz import deps
    pg = MagicMock(get_active_pipeline_run=AsyncMock(return_value=None))
    with patch.object(deps, "pg", lambda: pg):
        assert client.post("/api/datasets/upload", files=_files(template_csv())).status_code == 422
        assert client.post("/api/datasets/upload", files=_files(template_csv()), data={"confirm": "yes"}).status_code == 422
        r = client.post("/api/datasets/upload", files=_files("a,b\n1,2\n"), data={"confirm": "replace"})
        assert r.status_code == 422 and "Timestamp" in r.json()["detail"]
    pg.create_pipeline_run.assert_not_called()


def test_upload_conflicts_when_run_active(client):
    from app.viz import deps
    pg = MagicMock(get_active_pipeline_run=AsyncMock(return_value={"id": "busy"}))
    with patch.object(deps, "pg", lambda: pg):
        r = client.post("/api/datasets/upload", files=_files(template_csv()), data={"confirm": "replace"})
    assert r.status_code == 409


def test_upload_launches_runner_with_saved_files(client, tmp_path):
    from app.viz import deps
    pg = MagicMock(get_active_pipeline_run=AsyncMock(return_value=None), create_pipeline_run=AsyncMock(return_value="RID"))
    launched = {}
    class FakeRunner:
        def __init__(self, *a, **k): pass
        async def run(self, run_id, csv_path, patterns_path, name):
            launched.update(run_id=run_id, csv=csv_path, patterns=patterns_path, name=name)
    with patch.object(deps, "pg", lambda: pg), patch.object(deps, "neo4j", lambda: MagicMock()), \
         patch("app.api.endpoints.DatasetRunner", FakeRunner):
        files = dict(_files(template_csv()), patterns=("p.txt", io.BytesIO(b"BEGIN LAUNDERING ATTEMPT - CYCLE\n"), "text/plain"))
        r = client.post("/api/datasets/upload", files=files, data={"confirm": "replace"})
    assert r.status_code == 200 and r.json()["run_id"] == "RID"
    assert launched["run_id"] == "RID" and launched["name"] == "t.csv"
    assert launched["csv"].exists() and launched["patterns"].exists() and "RID" in str(launched["csv"])
```

- [ ] **Step 2: Implement**

`requirements.txt`: add `python-multipart>=0.0.9` under the FastAPI block.

`app/schemas/api.py`: add

```python
class UploadAccepted(BaseModel):
    run_id: str
```

`app/api/endpoints.py` — imports: `import shutil`, `from pathlib import Path`, `from fastapi import UploadFile, File, Form, BackgroundTasks`, `from fastapi.responses import PlainTextResponse`, `from app.core.config import settings`, `from app.services.dataset_runner import DatasetRunner`, `from app.services import dataset_validation`, `from app.schemas.api import UploadAccepted`, `from app.db.redis import get_redis`. Then append:

```python
@router.get("/datasets/current")
async def dataset_current():
    row = await viz_deps.pg().get_app_meta("dataset")
    if row is None:
        raise HTTPException(status_code=404, detail="no dataset recorded")
    return row


@router.get("/datasets/template")
async def dataset_template():
    return PlainTextResponse(dataset_validation.template_csv(), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="flowgraph-template.csv"'})


def _save_upload(upload: UploadFile, dest: Path, max_bytes: int) -> int:
    """Stream to disk with a size cap; returns bytes written."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(dest, "wb") as out:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"file exceeds {max_bytes // (1024 * 1024)} MB")
            out.write(chunk)
    return written


@router.post("/datasets/upload", response_model=UploadAccepted)
async def dataset_upload(
    background: BackgroundTasks,
    transactions: UploadFile = File(...),
    patterns: Optional[UploadFile] = File(None),
    confirm: str = Form(""),
):
    """Replace the loaded graph with an uploaded transaction CSV and run the
    pipeline on it. Validation happens before anything is touched; the wipe only
    starts inside the background job."""
    if confirm != "replace":
        raise HTTPException(status_code=422, detail='confirm must be "replace" — this wipes the current graph')
    pg = viz_deps.pg()
    if await pg.get_active_pipeline_run():
        raise HTTPException(status_code=409, detail="a pipeline run is already active")
    max_bytes = int(settings.UPLOAD_MAX_MB) * 1024 * 1024
    staging = Path(settings.UPLOAD_DIR) / "staging"
    csv_path = staging / (transactions.filename or "transactions.csv")
    _save_upload(transactions, csv_path, max_bytes)
    check = dataset_validation.validate_transactions_csv(csv_path)
    if not check["ok"]:
        csv_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=check["error"])
    patterns_path: Optional[Path] = None
    if patterns is not None and patterns.filename:
        patterns_path = staging / patterns.filename
        _save_upload(patterns, patterns_path, max_bytes)

    run_id = await pg.create_pipeline_run()
    run_dir = Path(settings.UPLOAD_DIR) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    final_csv = run_dir / csv_path.name
    shutil.move(str(csv_path), final_csv)
    final_patterns = None
    if patterns_path is not None:
        final_patterns = run_dir / patterns_path.name
        shutil.move(str(patterns_path), final_patterns)

    runner = DatasetRunner(viz_deps.neo4j(), get_redis(), pg, settings)
    background.add_task(runner.run, run_id, final_csv, final_patterns, transactions.filename or "upload.csv")
    return {"run_id": run_id}
```

Check `app/db/redis.py` for the name of the app-level Redis accessor (`get_redis` is what `graph_service` used to import) — it returns the `redis.asyncio` client; `ingest_for_training` expects an object with `bulk_add_edges_to_timeseries` (the *full* `db.redis.RedisClient`). Use the full client: add to `app/viz/deps.py` a `RedisClient` initialised at startup (`_redis = RedisClient(); await _redis.initialize()`, closed at shutdown, exposed as `deps.redis()`), and pass `viz_deps.redis()` to the runner instead of `get_redis()`.

- [ ] **Step 3: Verify, live-check the safe paths, commit**

`python3 -m pytest tests/test_datasets_api.py -q -p no:cacheprovider` → 4 passed; full suite green.

Restart uvicorn. Safe live checks (nothing is reset):
- `curl -s localhost:8000/api/datasets/current` → the IBM dataset row.
- `curl -s -D - localhost:8000/api/datasets/template | head -5` → `text/csv`, header line.
- `curl -s -X POST -F transactions=@<(printf 'a,b\n1,2\n') localhost:8000/api/datasets/upload` → 422 confirm message; with `-F confirm=replace` → 422 mentioning `Timestamp`.

```bash
git add requirements.txt app/schemas/api.py app/api/endpoints.py app/viz/deps.py tests/test_datasets_api.py
git commit -m "feat(api): dataset upload endpoints — template, current, validated replace-and-run" -m "$TRAILER"
```

---

### Task 5: Frontend upload view

**Files:**
- Modify: `Frontend/src/services/dataSource.js`, `Frontend/src/services/dataSource.test.js`, `Frontend/src/App.jsx`
- Rewrite: `Frontend/src/components/UploadView.jsx`

**Interfaces:**
- `source.datasets.current()`, `source.datasets.templateUrl()` (string | null), `source.datasets.upload({transactions, patterns}, onProgress) -> {run_id}`.

- [ ] **Step 1: Failing test** — append to `dataSource.test.js` inside the `createSnapshotSource` describe:

```js
  it('exposes the dataset from meta and rejects uploads', async () => {
    const s2 = createSnapshotSource(path => Promise.resolve({ 'meta.json': { dataset: { name: 'T' } } }[path]))
    expect((await s2.datasets.current()).name).toBe('T')
    expect(s2.datasets.templateUrl()).toBeNull()
    await expect(s2.datasets.upload({})).rejects.toBeInstanceOf(NotInSnapshotError)
  })
```

- [ ] **Step 2: Data source** — in `createLiveSource` add:

```js
    datasets: {
      current: () => get('/datasets/current'),
      templateUrl: () => `${base}/datasets/template`,
      upload: ({ transactions, patterns }, onProgress) => {
        const fd = new FormData()
        fd.append('transactions', transactions)
        if (patterns) fd.append('patterns', patterns)
        fd.append('confirm', 'replace')
        return http.post('/datasets/upload', fd, {
          timeout: 0,
          onUploadProgress: e => onProgress?.(e.total ? e.loaded / e.total : 0),
        }).then(r => r.data)
      },
    },
```

and in `createSnapshotSource`:

```js
    datasets: {
      current: () => load('meta.json').then(m => m?.dataset ?? null),
      templateUrl: () => null,
      upload: unsupported('Uploading a dataset'),
    },
```

- [ ] **Step 3: The view** — `Frontend/src/components/UploadView.jsx`:

```jsx
import { useEffect, useState } from 'react'
import { PageHeader, ErrorNote, DemoNote, Skeleton } from './ui'
import { useDataSource } from '../services/DataSourceProvider'
import { useAsync } from '../hooks/useAsync'
import { fmtDay } from '../lib/format'

const STAGES = ['reset', 'ingest', 'pagerank', 'louvain', 'cycle', 'gnn', 'aggregate']

export default function UploadView({ onNav }) {
  const { source, mode } = useDataSource()
  const current = useAsync(() => source.datasets.current(), [source.mode])
  const [transactions, setTransactions] = useState(null)
  const [patterns, setPatterns] = useState(null)
  const [confirmed, setConfirmed] = useState(false)
  const [progress, setProgress] = useState(null)      // upload progress 0..1
  const [run, setRun] = useState(null)                // pipeline_runs row
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!run || run.status === 'completed' || run.status === 'failed') return
    const t = window.setInterval(async () => {
      try { const s = await source.pipeline.runStatus(run.id); setRun(s) } catch { /* keep polling */ }
    }, 1500)
    return () => window.clearInterval(t)
  }, [run, source])

  const submit = async (e) => {
    e.preventDefault()
    if (!transactions || !confirmed) return
    setError(null); setProgress(0)
    try {
      const { run_id } = await source.datasets.upload({ transactions, patterns }, setProgress)
      setProgress(null)
      setRun({ id: run_id, status: 'queued', progress: 0, stage: null })
    } catch (err) { setProgress(null); setError(err) }
  }

  if (mode !== 'live') {
    return (
      <div className="px-8 pt-4">
        <PageHeader title="Analyze a file" subtitle="Run the whole pipeline on your own transactions" />
        <DemoNote>Uploading a dataset needs a running backend — the demo is a static snapshot.</DemoNote>
      </div>
    )
  }

  const pct = Math.round(((run?.progress) || 0) * 100)
  const ds = current.data
  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <PageHeader title="Analyze a file" subtitle="Replace the loaded graph with your transactions and run PageRank → Louvain → cycles → GNN → aggregation">
        {current.loading ? <Skeleton className="h-4 w-40" /> : ds ? (
          <span className="text-[12px] text-ink-3">
            Loaded now: <span className="font-mono text-ink">{ds.name}</span>
            {ds.start_ts ? ` · ${fmtDay(ds.start_ts)} – ${fmtDay(ds.end_ts)}` : ''}{ds.labelled ? ' · labelled' : ' · unlabelled'}
          </span>
        ) : null}
      </PageHeader>

      <form onSubmit={submit} className="max-w-2xl space-y-6 px-8 pb-10">
        <section className="space-y-2">
          <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">1 · Transactions CSV (required)</p>
          <p className="text-[12px] text-ink-3">
            IBM AML layout, in this column order: <span className="font-mono text-[11px]">Timestamp, From Bank, Account, To Bank, Account, Amount Received, Receiving Currency, Amount Paid, Payment Currency, Payment Format, Is Laundering</span>.
            {' '}<a href={source.datasets.templateUrl()} className="font-medium text-accent hover:opacity-70">Download a template →</a>
          </p>
          <input type="file" accept=".csv,text/csv" onChange={e => setTransactions(e.target.files?.[0] ?? null)} className="text-[12px] text-ink-2" />
        </section>

        <section className="space-y-2">
          <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-ink-4">2 · Patterns file (optional)</p>
          <p className="text-[12px] text-ink-3">An IBM-style <span className="font-mono text-[11px]">*_Patterns.txt</span> with known laundering attempts. With it, the Pipeline view can show precision and recall; without it the engine still scores every account.</p>
          <input type="file" accept=".txt,text/plain" onChange={e => setPatterns(e.target.files?.[0] ?? null)} className="text-[12px] text-ink-2" />
        </section>

        <section className="space-y-3 rounded-md border border-line-2 px-4 py-3">
          <label className="flex items-start gap-3 text-[12px] text-ink-2">
            <input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} className="mt-0.5 accent-[var(--accent)]" />
            <span>I understand this <strong>replaces the currently loaded graph</strong> (Neo4j, Redis and all risk flags). To get the IBM demo back, re-run the ingest described in the README.</span>
          </label>
          <button type="submit" disabled={!transactions || !confirmed || progress != null || (run && run.status !== 'completed' && run.status !== 'failed')}
            className="text-[13px] font-medium text-accent hover:opacity-70 disabled:opacity-40">
            {progress != null ? `Uploading… ${Math.round(progress * 100)}%` : 'Upload and run the pipeline →'}
          </button>
          {error && <ErrorNote error={error} />}
        </section>

        {run && (
          <section className="space-y-2">
            <div className="flex items-center gap-3 text-[12px] text-ink-3">
              <span className="font-mono">{run.status}{run.stage ? ` · ${run.stage}` : ''}</span>
              <span className="h-1 w-40 overflow-hidden rounded bg-hover"><span className="block h-full bg-accent transition-all" style={{ width: `${pct}%` }} /></span>
              <span className="font-mono text-[11px] tnum">{pct}%</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {STAGES.map(s => <span key={s} className={`font-mono text-[10px] uppercase ${run.stage === s ? 'text-accent' : 'text-ink-4'}`}>{s}</span>)}
            </div>
            {run.status === 'failed' && <ErrorNote error={{ message: `Failed at ${run.stage}: ${run.error}` }} />}
            {run.status === 'completed' && (
              <p className="text-[12px] text-ink-2">Done — {run.counts?.gnn_scored?.toLocaleString?.() ?? ''} accounts scored, {run.counts?.marked ?? 0} marked.
                {' '}<button type="button" onClick={() => onNav('pipeline')} className="font-medium text-accent hover:opacity-70">Open the Pipeline view →</button></p>
            )}
          </section>
        )}
      </form>
    </div>
  )
}
```

In `App.jsx` no change is needed beyond the plain lazy import already in place.

- [ ] **Step 4: Verify and commit**

`npm test` (11+ tests pass), `npm run lint` (0), `npm run build`. Live: with the backend up and `VITE_API_BASE_URL` set, `#/upload` shows the loaded IBM dataset row, the template link downloads, submitting without the checkbox is disabled, and submitting a junk CSV shows the 422 message. **Do not submit a valid file against the dev stack.** Demo mode shows the DemoNote.

```bash
cd Frontend
git add src/services/dataSource.js src/services/dataSource.test.js src/components/UploadView.jsx
git commit -m "feat(frontend): upload view — replace the graph with your own CSV and watch the pipeline run" -m "$TRAILER"
```

---

## Self-review against the spec

- §6.10 endpoints (current/template/upload, 409/413/422 rules, staging under `uploads/`, job stages, `reset_stores` public, `truth.reload`, live feature build + cache repoint, `app_meta` rewrite, cache invalidation) → Tasks 1–4. §8.3 Upload view (drop zone → file inputs, template, optional patterns, confirm checkbox, staged progress, navigate to Pipeline) → Task 5; the nav item is already live-only (Plan B). §10 error handling: validation before any store is touched; stage failures recorded; the UI states the consequences before confirm.
- Not automated on purpose: the destructive end-to-end run (documented in Global Constraints; unit-tested with fakes).
- Names consistent: `reset_stores`, `ingest_for_training(patterns_path=None)`, `truth.reload/DEFAULT`, `clear_risk_flags`, `PipelineRunner(redis=, feature_source=)`, `DatasetRunner.run(run_id, csv_path, patterns_path, name)`, `dataset_validation.validate_transactions_csv/template_csv/CSV_COLUMNS`, `viz_deps.redis()`, `source.datasets.current/templateUrl/upload`.
