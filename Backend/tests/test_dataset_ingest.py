"""
Tests for the GNN training ingestion path (ml/datasets/ibm_aml.py) and the
pipelined Redis writer it uses.

The behaviour that matters: all 8 typologies get loaded (not just CYCLE), the
background cap is honourable and configurable, and memory stays flat because
rows are flushed rather than accumulated.
"""

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from db.redis import RedisClient
from ml.datasets.ibm_aml import (
    ALL_TYPOLOGIES,
    TrainingIngestStats,
    ingest_for_training,
    load_pattern_accounts,
)


PATTERNS_PATH = Path("benchmarks/data/HI-Small_Patterns.txt")
CSV_PATH = Path("benchmarks/data/HI-Small_Trans.csv")

CSV_HEADER = [
    "Timestamp", "From Bank", "Account", "To Bank", "Account",
    "Amount Received", "Receiving Currency", "Amount Paid",
    "Payment Currency", "Payment Format", "Is Laundering",
]


class FakeNeo4j:
    """Records every row handed to the bulk writer."""

    def __init__(self):
        self.rows: List[Dict[str, Any]] = []
        self.flushes = 0
        self.pagerank_calls: List[Dict[str, Any]] = []

    async def bulk_upsert_transactions(self, transactions, batch_size=1000):
        self.rows.extend(transactions)
        self.flushes += 1
        return len(transactions)

    async def recompute_pagerank_full(self, window_days=365, reference_time=None, **kw):
        self.pagerank_calls.append(
            {"window_days": window_days, "reference_time": reference_time}
        )
        return len({r["sender_id"] for r in self.rows} | {r["receiver_id"] for r in self.rows})


class FakeRedis:
    def __init__(self):
        self.rows: List[Dict[str, Any]] = []

    async def bulk_add_edges_to_timeseries(self, edges, batch_size=1000):
        self.rows.extend(edges)
        return len(edges)


def _write_csv(path: Path, rows: List[List[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)


def _row(ts: str, from_bank: str, from_acct: str, to_bank: str, to_acct: str,
         amount: str = "100.00", laundering: str = "0") -> List[str]:
    return [
        ts, from_bank, from_acct, to_bank, to_acct,
        amount, "US Dollar", amount, "US Dollar", "ACH", laundering,
    ]


def _patterns_file(path: Path, blocks: List[tuple]) -> None:
    """Write a minimal patterns file. blocks = [(typology, [csv rows])]."""
    lines: List[str] = []
    for typology, rows in blocks:
        lines.append(f"BEGIN LAUNDERING ATTEMPT - {typology}:  test")
        for row in rows:
            lines.append(",".join(row))
        lines.append(f"END LAUNDERING ATTEMPT - {typology}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Pipelined Redis writer
# ---------------------------------------------------------------------------


class FakePipeline:
    def __init__(self, store, log):
        self.store = store
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def zadd(self, key, mapping):
        self.log.append(("zadd", key))
        self.store.setdefault(key, []).extend(mapping.items())

    def expire(self, key, ttl):
        self.log.append(("expire", key))

    async def execute(self):
        return []


class PipelineRedis:
    def __init__(self):
        self.store: Dict[str, list] = {}
        self.log: List[tuple] = []
        self.pipelines = 0

    def pipeline(self, transaction=False):
        self.pipelines += 1
        return FakePipeline(self.store, self.log)


@pytest.mark.asyncio
class TestBulkRedisWriter:
    async def test_member_encoding_matches_the_streaming_writer(self):
        """get_all_account_volumes parses "amount|ts" — this must produce it."""
        client = RedisClient()
        fake = PipelineRedis()
        client.client = fake
        ts = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)

        await client.bulk_add_edges_to_timeseries([
            {"sender_id": "a", "receiver_id": "b", "amount_cents": 5000,
             "timestamp_utc": ts},
        ])

        members = fake.store["edge:a:b"]
        assert members == [(f"5000|{int(ts.timestamp())}", int(ts.timestamp()))]

    async def test_one_pipeline_per_batch(self):
        client = RedisClient()
        fake = PipelineRedis()
        client.client = fake
        edges = [
            {"sender_id": f"a{i}", "receiver_id": f"b{i}", "amount_cents": 10,
             "timestamp_utc": 1_700_000_000}
            for i in range(250)
        ]

        written = await client.bulk_add_edges_to_timeseries(edges, batch_size=100)

        assert written == 250
        assert fake.pipelines == 3

    async def test_expire_issued_once_per_distinct_key(self):
        """A busy pair must not re-issue EXPIRE for every transaction."""
        client = RedisClient()
        fake = PipelineRedis()
        client.client = fake
        edges = [
            {"sender_id": "a", "receiver_id": "b", "amount_cents": i,
             "timestamp_utc": 1_700_000_000 + i}
            for i in range(20)
        ]

        await client.bulk_add_edges_to_timeseries(edges, batch_size=100)

        expires = [entry for entry in fake.log if entry[0] == "expire"]
        zadds = [entry for entry in fake.log if entry[0] == "zadd"]
        assert len(zadds) == 20
        assert len(expires) == 1

    async def test_accepts_integer_timestamps(self):
        client = RedisClient()
        fake = PipelineRedis()
        client.client = fake

        await client.bulk_add_edges_to_timeseries([
            {"sender_id": "a", "receiver_id": "b", "amount_cents": 1,
             "timestamp_utc": 1_700_000_000},
        ])

        assert fake.store["edge:a:b"][0][1] == 1_700_000_000

    async def test_empty_input_is_a_noop(self):
        client = RedisClient()
        client.client = PipelineRedis()
        assert await client.bulk_add_edges_to_timeseries([]) == 0

    async def test_rejects_bad_batch_size(self):
        client = RedisClient()
        client.client = PipelineRedis()
        with pytest.raises(ValueError, match="batch_size"):
            await client.bulk_add_edges_to_timeseries(
                [{"sender_id": "a", "receiver_id": "b", "amount_cents": 1,
                  "timestamp_utc": 1}], batch_size=0,
            )


# ---------------------------------------------------------------------------
# Training ingest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestIngestForTraining:
    async def test_loads_non_cycle_typologies(self, tmp_path):
        """The core fix: FAN-OUT accounts must reach the graph too.

        The benchmark ingestor uses load_cycle_groups and would ignore these.
        """
        fan_out = [
            _row("2022/09/01 00:06", "021174", "800737690", "012", "80011F990",
                 laundering="1"),
            _row("2022/09/01 04:33", "021174", "800737690", "020", "80020C5B0",
                 laundering="1"),
        ]
        patterns = tmp_path / "patterns.txt"
        _patterns_file(patterns, [("FAN-OUT", fan_out)])

        csv_file = tmp_path / "trans.csv"
        _write_csv(csv_file, fan_out + [
            _row("2022/09/02 10:00", "999", "unrelated1", "888", "unrelated2"),
        ])

        neo4j = FakeNeo4j()
        stats = await ingest_for_training(
            csv_file, patterns, neo4j, recompute_pagerank=False
        )

        assert stats.groups_loaded == 1
        assert stats.pattern_accounts == 3        # 1 source + 2 destinations
        assert stats.pattern_rows_written == 2
        assert "FAN-OUT" in stats.typology_counts

    async def test_all_eight_typologies_are_accepted(self, tmp_path):
        blocks = []
        rows = []
        for i, typology in enumerate(ALL_TYPOLOGIES):
            block = [
                _row(f"2022/09/0{(i % 9) + 1} 00:0{i}", f"bank{i}", f"acct{i}a",
                     f"bank{i}", f"acct{i}b", laundering="1")
            ]
            blocks.append((typology, block))
            rows.extend(block)

        patterns = tmp_path / "patterns.txt"
        _patterns_file(patterns, blocks)
        csv_file = tmp_path / "trans.csv"
        _write_csv(csv_file, rows)

        neo4j = FakeNeo4j()
        stats = await ingest_for_training(
            csv_file, patterns, neo4j, recompute_pagerank=False
        )

        assert stats.groups_loaded == len(ALL_TYPOLOGIES)
        assert set(stats.typology_counts) == set(ALL_TYPOLOGIES)

    async def test_background_cap_is_honoured(self, tmp_path):
        patterns = tmp_path / "patterns.txt"
        _patterns_file(patterns, [("CYCLE", [
            _row("2022/09/01 00:00", "1", "p1", "2", "p2", laundering="1"),
        ])])

        background = [
            _row(f"2022/09/02 00:{i:02d}", "9", f"bg{i}", "8", f"bg{i}x")
            for i in range(10)
        ]
        csv_file = tmp_path / "trans.csv"
        _write_csv(csv_file, [
            _row("2022/09/01 00:00", "1", "p1", "2", "p2", laundering="1"),
        ] + background)

        neo4j = FakeNeo4j()
        stats = await ingest_for_training(
            csv_file, patterns, neo4j, max_background_rows=4,
            recompute_pagerank=False,
        )

        assert stats.background_rows_written == 4
        assert stats.background_rows_skipped == 6
        # Pattern rows are never subject to the cap.
        assert stats.pattern_rows_written == 1

    async def test_no_cap_writes_everything(self, tmp_path):
        patterns = tmp_path / "patterns.txt"
        _patterns_file(patterns, [("CYCLE", [
            _row("2022/09/01 00:00", "1", "p1", "2", "p2", laundering="1"),
        ])])
        csv_file = tmp_path / "trans.csv"
        _write_csv(csv_file, [
            _row(f"2022/09/02 00:{i:02d}", "9", f"bg{i}", "8", f"bg{i}x")
            for i in range(25)
        ])

        neo4j = FakeNeo4j()
        stats = await ingest_for_training(
            csv_file, patterns, neo4j, max_background_rows=None,
            recompute_pagerank=False,
        )

        assert stats.background_rows_written == 25
        assert stats.background_rows_skipped == 0

    async def test_flushes_in_batches_rather_than_accumulating(self, tmp_path):
        """Memory must stay flat — rows are flushed, not held to the end."""
        patterns = tmp_path / "patterns.txt"
        _patterns_file(patterns, [("CYCLE", [
            _row("2022/09/01 00:00", "1", "p1", "2", "p2", laundering="1"),
        ])])
        csv_file = tmp_path / "trans.csv"
        _write_csv(csv_file, [
            _row(f"2022/09/02 {i // 60:02d}:{i % 60:02d}", "9", f"bg{i}", "8", f"bg{i}x")
            for i in range(50)
        ])

        neo4j = FakeNeo4j()
        await ingest_for_training(
            csv_file, patterns, neo4j, batch_size=10, recompute_pagerank=False
        )

        assert neo4j.flushes >= 5
        assert len(neo4j.rows) == 50

    async def test_redis_receives_the_same_rows(self, tmp_path):
        """Without this, 12 of the 29 features would be zero."""
        patterns = tmp_path / "patterns.txt"
        _patterns_file(patterns, [("CYCLE", [
            _row("2022/09/01 00:00", "1", "p1", "2", "p2", laundering="1"),
        ])])
        csv_file = tmp_path / "trans.csv"
        _write_csv(csv_file, [
            _row("2022/09/01 00:00", "1", "p1", "2", "p2", laundering="1"),
            _row("2022/09/02 00:00", "9", "bg1", "8", "bg2"),
        ])

        neo4j, redis = FakeNeo4j(), FakeRedis()
        stats = await ingest_for_training(
            csv_file, patterns, neo4j, redis_client=redis, recompute_pagerank=False
        )

        assert stats.redis_rows_written == 2
        assert len(redis.rows) == len(neo4j.rows)

    async def test_pagerank_anchors_to_dataset_time_not_now(self, tmp_path):
        """2022 data with a now-anchored window would score nothing."""
        patterns = tmp_path / "patterns.txt"
        _patterns_file(patterns, [("CYCLE", [
            _row("2022/09/01 00:00", "1", "p1", "2", "p2", laundering="1"),
        ])])
        csv_file = tmp_path / "trans.csv"
        _write_csv(csv_file, [
            _row("2022/09/01 00:00", "1", "p1", "2", "p2", laundering="1"),
            _row("2022/09/10 00:00", "1", "p1", "2", "p3", laundering="1"),
        ])

        neo4j = FakeNeo4j()
        stats = await ingest_for_training(
            csv_file, patterns, neo4j, recompute_pagerank=True
        )

        assert len(neo4j.pagerank_calls) == 1
        call = neo4j.pagerank_calls[0]
        assert call["reference_time"].year == 2022
        assert call["window_days"] >= 9
        assert stats.pagerank_scores_written > 0

    async def test_row_limit_stops_early(self, tmp_path):
        patterns = tmp_path / "patterns.txt"
        _patterns_file(patterns, [("CYCLE", [
            _row("2022/09/01 00:00", "1", "p1", "2", "p2", laundering="1"),
        ])])
        csv_file = tmp_path / "trans.csv"
        _write_csv(csv_file, [
            _row(f"2022/09/02 00:{i:02d}", "9", f"bg{i}", "8", f"bg{i}x")
            for i in range(30)
        ])

        neo4j = FakeNeo4j()
        stats = await ingest_for_training(
            csv_file, patterns, neo4j, row_limit=7, recompute_pagerank=False
        )

        assert stats.rows_scanned == 7

    async def test_malformed_rows_are_counted_not_fatal(self, tmp_path):
        patterns = tmp_path / "patterns.txt"
        _patterns_file(patterns, [("CYCLE", [
            _row("2022/09/01 00:00", "1", "p1", "2", "p2", laundering="1"),
        ])])
        csv_file = tmp_path / "trans.csv"
        _write_csv(csv_file, [
            _row("2022/09/01 00:00", "1", "p1", "2", "p2", laundering="1"),
            _row("not-a-date", "9", "bg1", "8", "bg2"),
            _row("2022/09/02 00:00", "9", "bg3", "8", "bg4", amount="0.00"),
        ])

        neo4j = FakeNeo4j()
        stats = await ingest_for_training(
            csv_file, patterns, neo4j, recompute_pagerank=False
        )

        assert stats.skipped_bad_rows == 2
        assert stats.total_written == 1

    async def test_missing_csv_raises_with_guidance(self, tmp_path):
        patterns = tmp_path / "patterns.txt"
        _patterns_file(patterns, [])
        with pytest.raises(FileNotFoundError, match="Kaggle"):
            await ingest_for_training(tmp_path / "nope.csv", patterns, FakeNeo4j())

    async def test_rejects_bad_batch_size(self, tmp_path):
        patterns = tmp_path / "patterns.txt"
        _patterns_file(patterns, [])
        csv_file = tmp_path / "trans.csv"
        _write_csv(csv_file, [])
        with pytest.raises(ValueError, match="batch_size"):
            await ingest_for_training(csv_file, patterns, FakeNeo4j(), batch_size=0)


class TestStatsSummary:
    def test_summary_mentions_the_key_counters(self):
        stats = TrainingIngestStats(
            rows_scanned=100, pattern_rows_written=10, background_rows_written=20
        )
        text = stats.summary()
        assert "scanned=100" in text
        assert "pattern=10" in text
        assert stats.total_written == 30


@pytest.mark.skipif(
    not PATTERNS_PATH.exists(),
    reason="IBM AML patterns file not present",
)
class TestAgainstRealPatternsFile:
    def test_all_typologies_beat_cycle_only_substantially(self):
        """Quantifies the win: how many more positives all-typologies unlocks."""
        every, group_count, per_typology = load_pattern_accounts(
            PATTERNS_PATH, ALL_TYPOLOGIES
        )
        cycle_only, cycle_groups, _ = load_pattern_accounts(PATTERNS_PATH, ["CYCLE"])

        assert group_count > cycle_groups
        assert len(every) > len(cycle_only) * 3
        assert len(per_typology) == len(ALL_TYPOLOGIES)
