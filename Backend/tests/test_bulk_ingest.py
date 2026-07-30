"""
Tests for the bulk dataset-ingestion path: sparse PageRank and batched writes.

The streaming path is untouched by all of this — these exist so millions of
rows can be loaded in minutes instead of hours.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List

import numpy as np
import pytest

from algorithms.pagerank import compute_pagerank_sparse, compute_weighted_pagerank
from db.neo4j import Neo4jClient


REF = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)


class FakeSession:
    """Captures every query and its parameters."""

    def __init__(self, calls: List[tuple], records: Any = None):
        self.calls = calls
        self.records = records or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def run(self, query, **kwargs):
        text = query.text if hasattr(query, "text") else str(query)
        self.calls.append((text, kwargs))

        records = self.records

        class FakeResult:
            def __aiter__(self):
                async def gen():
                    for record in records:
                        yield record

                return gen()

            async def data(self):
                return records

        return FakeResult()


class FakeDriver:
    def __init__(self, session):
        self._session = session

    def session(self, database=None):
        return self._session


def _client(calls: List[tuple], records: Any = None) -> Neo4jClient:
    client = Neo4jClient()
    client.driver = FakeDriver(FakeSession(calls, records))
    return client


def _edge_record(
    src: str, dst: str, total_amount: float, tx_count: int
) -> Dict[str, Any]:
    """A row shaped like one record of export_flows_to_edges.

    first_ts/last_ts are part of that contract — the feature builder derives
    account age from first_ts, since Account.created_at records ingest time.
    """
    epoch = int(REF.timestamp())
    return {
        "src": src,
        "dst": dst,
        "total_amount": total_amount,
        "tx_count": tx_count,
        "first_ts": epoch,
        "last_ts": epoch,
    }


def _txn(
    sender: str,
    receiver: str,
    amount_cents: int = 1000,
    txn_id: str = None,
    ts: Any = None,
) -> Dict[str, Any]:
    return {
        "sender_id": sender,
        "receiver_id": receiver,
        "amount_cents": amount_cents,
        "timestamp_utc": ts if ts is not None else REF,
        "rail": "ACH",
        "event_type": "SETTLEMENT",
        "transaction_id": txn_id or f"{sender}-{receiver}-{amount_cents}",
    }


# ---------------------------------------------------------------------------
# Sparse PageRank
# ---------------------------------------------------------------------------


class TestSparsePageRank:
    def test_agrees_with_dense_implementation(self):
        """Both must produce the same scores on a graph dense can handle.

        This is the guard that the sparse rewrite did not change the maths.
        """
        adjacency = {
            "a": {"b": 2.0, "c": 1.0},
            "b": {"c": 3.0},
            "c": {"a": 1.0},
        }
        edges = [
            (src, dst, weight)
            for src, targets in adjacency.items()
            for dst, weight in targets.items()
        ]

        dense = compute_weighted_pagerank(adjacency, max_iterations=200, tolerance=1e-12)
        sparse = compute_pagerank_sparse(edges, max_iterations=200, tolerance=1e-12)

        assert set(dense) == set(sparse)
        for node in dense:
            assert sparse[node] == pytest.approx(dense[node], abs=1e-9)

    def test_scores_sum_to_one(self):
        edges = [("a", "b", 1.0), ("b", "c", 1.0), ("c", "a", 1.0)]
        scores = compute_pagerank_sparse(edges)
        assert sum(scores.values()) == pytest.approx(1.0, abs=1e-6)

    def test_symmetric_cycle_gives_equal_scores(self):
        edges = [("a", "b", 1.0), ("b", "c", 1.0), ("c", "a", 1.0)]
        scores = compute_pagerank_sparse(edges)
        assert scores["a"] == pytest.approx(scores["b"], abs=1e-6)
        assert scores["b"] == pytest.approx(scores["c"], abs=1e-6)

    def test_hub_outranks_its_spokes(self):
        """Everyone pays the hub, so it should score highest."""
        edges = [(f"spoke{i}", "hub", 1.0) for i in range(10)]
        scores = compute_pagerank_sparse(edges)
        assert scores["hub"] > max(scores[f"spoke{i}"] for i in range(10))

    def test_duplicate_pairs_are_summed(self):
        """Matches FLOWS_TO semantics: repeated pairs accumulate weight."""
        summed = compute_pagerank_sparse([("a", "b", 3.0), ("a", "c", 1.0)])
        split = compute_pagerank_sparse(
            [("a", "b", 1.0), ("a", "b", 2.0), ("a", "c", 1.0)]
        )
        for node in summed:
            assert split[node] == pytest.approx(summed[node], abs=1e-9)

    def test_scales_past_the_dense_limit(self):
        """50k nodes would need ~20GB dense; sparse handles it in memory.

        This is the entire reason the function exists.
        """
        n = 50_000
        edges = [(f"a{i}", f"a{(i + 1) % n}", 1.0) for i in range(n)]

        scores = compute_pagerank_sparse(edges, max_iterations=5)

        assert len(scores) == n
        assert np.isfinite(list(scores.values())).all()
        assert sum(scores.values()) == pytest.approx(1.0, abs=1e-3)

    def test_dangling_node_mass_is_not_lost(self):
        """A sink with no outgoing edges must redistribute, not leak."""
        edges = [("a", "sink", 1.0), ("b", "sink", 1.0)]
        scores = compute_pagerank_sparse(edges)
        assert sum(scores.values()) == pytest.approx(1.0, abs=1e-6)

    def test_empty_edges_returns_empty(self):
        assert compute_pagerank_sparse([]) == {}

    def test_rejects_negative_weights(self):
        with pytest.raises(ValueError, match="non-negative"):
            compute_pagerank_sparse([("a", "b", -1.0)])

    def test_rejects_bad_damping_and_iterations(self):
        with pytest.raises(ValueError, match="damping"):
            compute_pagerank_sparse([("a", "b", 1.0)], damping=1.5)
        with pytest.raises(ValueError, match="max_iterations"):
            compute_pagerank_sparse([("a", "b", 1.0)], max_iterations=0)

    def test_rejects_malformed_edges(self):
        with pytest.raises(TypeError):
            compute_pagerank_sparse([("a", "b")])
        with pytest.raises(TypeError, match="strings"):
            compute_pagerank_sparse([(1, "b", 1.0)])


# ---------------------------------------------------------------------------
# Batched writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBulkUpsert:
    async def test_one_round_trip_per_batch(self):
        """The point of the method: 250 rows must not mean 250 queries."""
        calls: List[tuple] = []
        client = _client(calls)
        transactions = [_txn(f"a{i}", f"b{i}") for i in range(250)]

        written = await client.bulk_upsert_transactions(transactions, batch_size=100)

        assert written == 250
        assert len(calls) == 3          # 100 + 100 + 50

    async def test_uses_unwind_and_keeps_both_edge_types(self):
        calls: List[tuple] = []
        client = _client(calls)

        await client.bulk_upsert_transactions([_txn("a", "b")])

        query, params = calls[0]
        assert "UNWIND $rows AS row" in query
        assert "TRANSFER" in query
        assert "FLOWS_TO" in query
        # MERGE only — never plain CREATE, per the conventions.
        assert "CREATE (" not in query
        assert len(params["rows"]) == 1

    async def test_aggregates_are_incremented_not_overwritten(self):
        calls: List[tuple] = []
        client = _client(calls)

        await client.bulk_upsert_transactions([_txn("a", "b")])

        query, _ = calls[0]
        assert "f.tx_count     = f.tx_count + 1" in query
        assert "f.total_amount = f.total_amount + row.amount_cents" in query

    async def test_datetime_converted_to_unix_seconds(self):
        calls: List[tuple] = []
        client = _client(calls)

        await client.bulk_upsert_transactions([_txn("a", "b", ts=REF)])

        row = calls[0][1]["rows"][0]
        assert row["timestamp_utc"] == int(REF.timestamp())
        assert isinstance(row["timestamp_utc"], int)

    async def test_integer_timestamp_passes_through(self):
        calls: List[tuple] = []
        client = _client(calls)
        epoch = int(REF.timestamp())

        await client.bulk_upsert_transactions([_txn("a", "b", ts=epoch)])

        assert calls[0][1]["rows"][0]["timestamp_utc"] == epoch

    async def test_duplicate_txn_ids_deduped_within_batch(self):
        """Without dedup, FLOWS_TO aggregates would double-count the same payment."""
        calls: List[tuple] = []
        client = _client(calls)
        transactions = [
            _txn("a", "b", txn_id="same"),
            _txn("a", "b", txn_id="same"),
            _txn("a", "c", txn_id="other"),
        ]

        written = await client.bulk_upsert_transactions(transactions, batch_size=10)

        assert written == 2
        assert len(calls[0][1]["rows"]) == 2

    async def test_repeated_account_pair_is_kept(self):
        """Distinct payments between the same pair must all be written.

        UNWIND applies rows in order inside one transaction and MERGE sees
        earlier writes, so the aggregates accumulate correctly.
        """
        calls: List[tuple] = []
        client = _client(calls)
        transactions = [
            _txn("a", "b", amount_cents=100, txn_id="t1"),
            _txn("a", "b", amount_cents=200, txn_id="t2"),
        ]

        written = await client.bulk_upsert_transactions(transactions)

        assert written == 2
        assert len(calls[0][1]["rows"]) == 2

    async def test_does_not_compute_pagerank(self):
        """Skipping per-row PageRank is why this path is fast."""
        calls: List[tuple] = []
        client = _client(calls)

        await client.bulk_upsert_transactions([_txn("a", "b") for _ in range(5)])

        for query, _ in calls:
            assert "pagerank_score" not in query

    async def test_empty_input_is_a_noop(self):
        calls: List[tuple] = []
        client = _client(calls)

        assert await client.bulk_upsert_transactions([]) == 0
        assert calls == []

    async def test_without_driver_returns_zero(self):
        client = Neo4jClient()
        assert await client.bulk_upsert_transactions([_txn("a", "b")]) == 0

    async def test_rejects_bad_batch_size(self):
        client = _client([])
        with pytest.raises(ValueError, match="batch_size"):
            await client.bulk_upsert_transactions([_txn("a", "b")], batch_size=0)


@pytest.mark.asyncio
class TestRecomputePageRankFull:
    async def test_writes_scores_for_every_account(self):
        edges = [
            _edge_record("a", "b", 100.0, 1),
            _edge_record("b", "c", 200.0, 2),
            _edge_record("c", "a", 150.0, 1),
        ]
        calls: List[tuple] = []
        client = _client(calls, records=edges)

        written = await client.recompute_pagerank_full()

        assert written == 3
        write_calls = [c for c in calls if "pagerank_score" in c[0]]
        assert len(write_calls) == 1
        accounts = {u["account_id"] for u in write_calls[0][1]["updates"]}
        assert accounts == {"a", "b", "c"}

    async def test_scores_are_written_in_batches(self):
        edges = [
            _edge_record(f"a{i}", f"a{i + 1}", 10.0, 1)
            for i in range(24)
        ]
        calls: List[tuple] = []
        client = _client(calls, records=edges)

        written = await client.recompute_pagerank_full(write_batch_size=10)

        assert written == 25        # 25 distinct accounts
        write_calls = [c for c in calls if "pagerank_score" in c[0]]
        assert len(write_calls) == 3

    async def test_export_timeout_is_far_above_the_louvain_default(self):
        """Regression: the whole-graph export must not inherit the 120s budget.

        export_flows_to_edges defaults to LOUVAIN_EXPORT_TIMEOUT_SECONDS (120s),
        sized for the Louvain window. On the real HI-Small load, exporting
        1,010,384 FLOWS_TO edges blew through it and killed the run AFTER all
        5.08M rows had already been written.
        """
        from config import LOUVAIN_EXPORT_TIMEOUT_SECONDS

        captured: Dict[str, Any] = {}
        client = _client([], records=[])

        async def fake_export(**kwargs):
            captured.update(kwargs)
            return []

        client.export_flows_to_edges = fake_export
        await client.recompute_pagerank_full()

        assert "query_timeout_seconds" in captured, (
            "recompute_pagerank_full must pass its own export timeout"
        )
        assert captured["query_timeout_seconds"] > LOUVAIN_EXPORT_TIMEOUT_SECONDS
        assert captured["query_timeout_seconds"] >= 600

    async def test_export_timeout_is_overridable(self):
        captured: Dict[str, Any] = {}
        client = _client([], records=[])

        async def fake_export(**kwargs):
            captured.update(kwargs)
            return []

        client.export_flows_to_edges = fake_export
        await client.recompute_pagerank_full(export_timeout_seconds=42.0)

        assert captured["query_timeout_seconds"] == 42.0

    async def test_no_edges_writes_nothing(self):
        calls: List[tuple] = []
        client = _client(calls, records=[])

        assert await client.recompute_pagerank_full() == 0
        assert not [c for c in calls if "pagerank_score" in c[0]]

    async def test_without_driver_returns_zero(self):
        client = Neo4jClient()
        assert await client.recompute_pagerank_full() == 0


@pytest.mark.asyncio
class TestMaxFlowsToTimestamp:
    """Anchoring time windows on historical data depends on this."""

    async def _client_returning(self, record):
        class Result:
            async def single(self):
                return record

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def run(self, query, **kwargs):
                Session.last_query = query
                return Result()

        client = Neo4jClient()
        client.driver = FakeDriver(Session())
        return client, Session

    async def test_returns_the_max_timestamp(self):
        client, session = await self._client_returning({"max_ts": 1_663_459_200})
        assert await client.get_flows_to_timestamp() == 1_663_459_200
        assert "max(f.last_ts)" in session.last_query

    async def test_empty_graph_returns_none(self):
        client, _ = await self._client_returning({"max_ts": None})
        assert await client.get_flows_to_timestamp() is None

    async def test_no_record_returns_none(self):
        client, _ = await self._client_returning(None)
        assert await client.get_flows_to_timestamp() is None

    async def test_without_driver_returns_none(self):
        assert await Neo4jClient().get_flows_to_timestamp() is None
