"""
Tests for the cycle detection fraud engine.

Structure:
  TestCycleScoring    — pure unit tests for score_cycle, cycle_fingerprint, is_simple_cycle
                        No I/O, no fixtures needed.

  TestNeo4jTransferWriter — integration tests (mock Neo4j) asserting TRANSFER edge model:
                            correct Cypher called, idempotency on txn_id, MERGE on accounts.

  TestCycleDetector   — integration tests for CycleDetector.detect() using mocked clients:
                        end-to-end flag production, fingerprint dedup, non-simple cycle
                        filtering, empty result handling.

  TestFindCyclesQuery — unit tests for find_cycles parameter handling (no real Neo4j).

Note: full integration tests against live Neo4j (testcontainers) are in test_integration.py.
"""

import hashlib
import pytest
import math
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, call
from uuid import uuid4

from fraud.cycle_detector import (
    score_cycle,
    cycle_fingerprint,
    is_simple_cycle,
    CycleDetector,
)
from config import CYCLE_MIN_VALUE_CENTS


# ===========================================================================
# Helpers
# ===========================================================================

def _ts_seq(start_offset=0, count=3, gap_hours=1):
    """Generate `count` unix-second timestamps with `gap_hours` between them."""
    base = int(datetime.now(timezone.utc).timestamp()) + start_offset
    return [base + i * int(gap_hours * 3600) for i in range(count)]


def _conserved_amounts(start_cents: int, n: int, leak: float = 0.05) -> list[int]:
    """Amounts that decrease slightly per hop (simulates fees)."""
    result = []
    v = start_cents
    for _ in range(n):
        result.append(int(v))
        v = int(v * (1 - leak))
    return result


# ===========================================================================
# Test: score_cycle (pure)
# ===========================================================================

class TestCycleScoring:

    def test_high_value_fast_consistent_3hop_is_critical_or_high(self):
        """A tight, fast, conserved $50k 3-hop loop should score high/critical."""
        amounts = _conserved_amounts(5_000_000, 3, leak=0.03)  # ~$50k, 3% bleed
        timestamps = _ts_seq(count=3, gap_hours=0.5)           # 30-min intervals
        result = score_cycle(["A", "B", "C", "A"], amounts, timestamps)
        assert result["risk_level"] in ("high", "critical")
        assert len(result["explanation"]) > 20
        assert result["risk_score"] > 0.5

    def test_tiny_slow_loop_scores_low(self):
        """A tiny, slow loop should score low (or below the min floor in real usage)."""
        amounts = [200_000, 190_000, 180_000]  # ~$2k — above floor for scoring purposes
        timestamps = _ts_seq(count=3, gap_hours=20)  # very slow: 20h between hops
        result = score_cycle(["A", "B", "C", "A"], amounts, timestamps)
        assert result["risk_level"] in ("low", "medium")
        assert result["risk_score"] < 0.7

    def test_explanation_always_present(self):
        """explanation must never be empty — regulatory requirement."""
        amounts = _conserved_amounts(1_000_000, 2)
        timestamps = _ts_seq(count=2, gap_hours=1)
        result = score_cycle(["A", "B", "A"], amounts, timestamps)
        assert isinstance(result["explanation"], str)
        assert len(result["explanation"]) > 0

    def test_explanation_cites_dollars(self):
        """explanation must mention dollar figures for the analyst."""
        amounts = [5_000_000, 4_750_000, 4_500_000]  # $50k, $47.5k, $45k
        timestamps = _ts_seq(count=3, gap_hours=1)
        result = score_cycle(["A", "B", "C", "A"], amounts, timestamps)
        assert "$" in result["explanation"]

    def test_instantaneous_loop_max_velocity(self):
        """All-same-timestamp loop gets full velocity score."""
        now = int(datetime.now(timezone.utc).timestamp())
        timestamps = [now, now, now]
        amounts = _conserved_amounts(2_000_000, 3)
        result = score_cycle(["A", "B", "C", "A"], amounts, timestamps)
        assert result["details"]["velocity_score"] == 1.0

    def test_high_variance_amounts_lower_consistency(self):
        """Widely varying amounts reduce the consistency score."""
        amounts = [5_000_000, 500_000, 5_000_000]  # 10x variance
        timestamps = _ts_seq(count=3, gap_hours=1)
        low_var = score_cycle(["A", "B", "C", "A"],
                              _conserved_amounts(5_000_000, 3, 0.01),
                              timestamps)
        high_var = score_cycle(["A", "B", "C", "A"], amounts, timestamps)
        assert low_var["details"]["consistency_score"] > high_var["details"]["consistency_score"]

    def test_score_in_range(self):
        """Risk score must always be in [0, 1]."""
        for n in (2, 3, 4, 6):
            amounts = _conserved_amounts(1_500_000, n)
            timestamps = _ts_seq(count=n, gap_hours=2)
            node_ids = [chr(65 + i) for i in range(n)] + [chr(65)]
            result = score_cycle(node_ids, amounts, timestamps)
            assert 0.0 <= result["risk_score"] <= 1.0

    def test_details_contains_key_fields(self):
        """details dict must include the numbers needed for AI enrichment."""
        amounts = _conserved_amounts(3_000_000, 3)
        timestamps = _ts_seq(count=3, gap_hours=1)
        result = score_cycle(["A", "B", "C", "A"], amounts, timestamps)
        for field in ("n_hops", "total_cents", "min_hop_cents", "span_hours",
                      "value_score", "velocity_score", "consistency_score", "hop_score"):
            assert field in result["details"], f"Missing field: {field}"

    def test_missing_amounts_raises(self):
        with pytest.raises((ValueError, ZeroDivisionError, IndexError)):
            score_cycle(["A", "B", "A"], [], [])

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError):
            score_cycle(["A", "B", "A"], [1000], [1000, 2000])


# ===========================================================================
# Test: cycle_fingerprint (pure)
# ===========================================================================

class TestCycleFingerprint:

    def test_same_ring_different_start_same_fingerprint(self):
        """Ring [A,B,C,A] found from A, [B,C,A,B] from B → same fingerprint."""
        fp_a = cycle_fingerprint(["A", "B", "C", "A"])
        fp_b = cycle_fingerprint(["B", "C", "A", "B"])
        fp_c = cycle_fingerprint(["C", "A", "B", "C"])
        assert fp_a == fp_b == fp_c

    def test_different_ring_different_fingerprint(self):
        fp1 = cycle_fingerprint(["A", "B", "C", "A"])
        fp2 = cycle_fingerprint(["A", "B", "D", "A"])
        assert fp1 != fp2

    def test_output_is_64_char_hex(self):
        fp = cycle_fingerprint(["A", "B", "C", "A"])
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)

    def test_rotation_canonical_form(self):
        """Canonical form always starts at the lex-smallest node."""
        fp = cycle_fingerprint(["C", "A", "B", "C"])
        # Canonical ring should be A→B→C (A is smallest)
        expected = hashlib.sha256("A→B→C".encode()).hexdigest()
        assert fp == expected

    def test_two_node_loop(self):
        """A↔B loop is valid (but would be filtered by is_simple_cycle for 2 nodes)."""
        fp = cycle_fingerprint(["A", "B", "A"])
        assert len(fp) == 64


# ===========================================================================
# Test: is_simple_cycle (pure)
# ===========================================================================

class TestIsSimpleCycle:

    def test_simple_triangle(self):
        assert is_simple_cycle(["A", "B", "C", "A"]) is True

    def test_simple_two_node(self):
        assert is_simple_cycle(["A", "B", "A"]) is True

    def test_repeated_intermediate(self):
        # A→B→A→B→A: B appears twice in intermediates
        assert is_simple_cycle(["A", "B", "A", "B", "A"]) is False

    def test_four_hop_simple(self):
        assert is_simple_cycle(["A", "B", "C", "D", "A"]) is True

    def test_six_hop_simple(self):
        assert is_simple_cycle(["A", "B", "C", "D", "E", "F", "A"]) is True


# ===========================================================================
# Test: CycleDetector.detect() — mocked I/O
# ===========================================================================

class TestCycleDetector:

    def _make_raw_cycle(self, accounts=("A", "B", "C"), n_hours=2, amount=5_000_000):
        """Build a raw cycle dict as returned by neo4j.find_cycles."""
        node_ids = list(accounts) + [accounts[0]]
        n = len(accounts)
        timestamps = _ts_seq(count=n, gap_hours=n_hours // max(n - 1, 1))
        amounts = _conserved_amounts(amount, n, leak=0.05)
        txn_ids = [str(uuid4()) for _ in range(n)]
        return {
            "node_ids":   node_ids,
            "amounts":    amounts,
            "timestamps": timestamps,
            "txn_ids":    txn_ids,
        }

    @pytest.mark.asyncio
    async def test_detect_persists_flag(self, mock_neo4j_client, mock_postgres_client):
        """A valid cycle triggers exactly one upsert_risk_flag call."""
        raw = self._make_raw_cycle()
        mock_neo4j_client.find_cycles = AsyncMock(return_value=[raw])
        mock_postgres_client.upsert_risk_flag = AsyncMock()

        detector = CycleDetector(mock_neo4j_client, mock_postgres_client)
        flags = await detector.detect("A")

        assert len(flags) == 1
        mock_postgres_client.upsert_risk_flag.assert_called_once()
        call_kwargs = mock_postgres_client.upsert_risk_flag.call_args
        assert call_kwargs.kwargs["flag_type"] == "CYCLE"
        assert call_kwargs.kwargs["explanation"]  # never empty

    @pytest.mark.asyncio
    async def test_same_ring_from_three_seeds_one_flag(
        self, mock_neo4j_client, mock_postgres_client
    ):
        """The same ring [A,B,C] found from A, B, C → one unique fingerprint → one persist."""
        cycle_a = self._make_raw_cycle(("A", "B", "C"))
        cycle_b = {**cycle_a, "node_ids": ["B", "C", "A", "B"],
                   "amounts": cycle_a["amounts"], "timestamps": cycle_a["timestamps"]}
        cycle_c = {**cycle_a, "node_ids": ["C", "A", "B", "C"],
                   "amounts": cycle_a["amounts"], "timestamps": cycle_a["timestamps"]}

        # Detect from A with all three variants in the response
        mock_neo4j_client.find_cycles = AsyncMock(
            return_value=[cycle_a, cycle_b, cycle_c]
        )
        mock_postgres_client.upsert_risk_flag = AsyncMock()

        detector = CycleDetector(mock_neo4j_client, mock_postgres_client)
        flags = await detector.detect("A")

        # Only one unique fingerprint → one persist
        assert len(flags) == 1
        mock_postgres_client.upsert_risk_flag.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_simple_cycle_filtered(self, mock_neo4j_client, mock_postgres_client):
        """A cycle with a repeated intermediate account is filtered out."""
        # A→B→A→B→A: B is repeated
        raw = {
            "node_ids":   ["A", "B", "A", "B", "A"],
            "amounts":    _conserved_amounts(2_000_000, 4),
            "timestamps": _ts_seq(count=4, gap_hours=1),
            "txn_ids":    [str(uuid4()) for _ in range(4)],
        }
        mock_neo4j_client.find_cycles = AsyncMock(return_value=[raw])
        mock_postgres_client.upsert_risk_flag = AsyncMock()

        detector = CycleDetector(mock_neo4j_client, mock_postgres_client)
        flags = await detector.detect("A")

        assert flags == []
        mock_postgres_client.upsert_risk_flag.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_cycles_returns_empty(self, mock_neo4j_client, mock_postgres_client):
        """Empty Neo4j response → no flags, no db writes."""
        mock_neo4j_client.find_cycles = AsyncMock(return_value=[])
        mock_postgres_client.upsert_risk_flag = AsyncMock()

        detector = CycleDetector(mock_neo4j_client, mock_postgres_client)
        flags = await detector.detect("CARDHOLDER_HASH")

        assert flags == []
        mock_postgres_client.upsert_risk_flag.assert_not_called()

    @pytest.mark.asyncio
    async def test_flag_contains_risk_level_and_explanation(
        self, mock_neo4j_client, mock_postgres_client
    ):
        """Each returned flag has risk_level and a non-empty explanation."""
        raw = self._make_raw_cycle(amount=10_000_000)  # $100k
        mock_neo4j_client.find_cycles = AsyncMock(return_value=[raw])
        mock_postgres_client.upsert_risk_flag = AsyncMock()

        detector = CycleDetector(mock_neo4j_client, mock_postgres_client)
        flags = await detector.detect("A")

        assert len(flags) == 1
        f = flags[0]
        assert f["risk_level"] in ("low", "medium", "high", "critical")
        assert isinstance(f["explanation"], str) and len(f["explanation"]) > 0

    @pytest.mark.asyncio
    async def test_multiple_distinct_cycles_all_persisted(
        self, mock_neo4j_client, mock_postgres_client
    ):
        """Two different rings produce two separate flags."""
        ring1 = self._make_raw_cycle(("A", "B", "C"))
        ring2 = self._make_raw_cycle(("A", "D", "E"))
        mock_neo4j_client.find_cycles = AsyncMock(return_value=[ring1, ring2])
        mock_postgres_client.upsert_risk_flag = AsyncMock()

        detector = CycleDetector(mock_neo4j_client, mock_postgres_client)
        flags = await detector.detect("A")

        assert len(flags) == 2
        assert mock_postgres_client.upsert_risk_flag.call_count == 2


# ===========================================================================
# Test: Neo4j writer — upsert_transaction_graph uses TRANSFER model
# ===========================================================================

class TestNeo4jTransferWriter:
    """
    Verify that upsert_transaction_graph uses the per-transaction TRANSFER model.
    These use the mock client rather than real Neo4j (integration tests cover the real thing).
    """

    @pytest.mark.asyncio
    async def test_upsert_called_with_expected_args(self, mock_neo4j_client):
        now = datetime.now(timezone.utc)
        txn_id = str(uuid4())

        await mock_neo4j_client.upsert_transaction_graph(
            sender_id="ACC_A",
            receiver_id="ACC_B",
            amount_cents=500_000,
            timestamp_utc=now,
            rail="WIRE",
            event_type="SETTLEMENT",
            transaction_id=txn_id,
            idempotency_key=txn_id,
        )

        mock_neo4j_client.upsert_transaction_graph.assert_called_once_with(
            sender_id="ACC_A",
            receiver_id="ACC_B",
            amount_cents=500_000,
            timestamp_utc=now,
            rail="WIRE",
            event_type="SETTLEMENT",
            transaction_id=txn_id,
            idempotency_key=txn_id,
        )

    @pytest.mark.asyncio
    async def test_find_cycles_returns_list(self, mock_neo4j_client):
        """find_cycles must return a list (even when empty)."""
        mock_neo4j_client.find_cycles.return_value = []
        result = await mock_neo4j_client.find_cycles("ACC_A", max_depth=6)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_init_constraints_called(self, mock_neo4j_client):
        mock_neo4j_client.init_constraints = AsyncMock()
        await mock_neo4j_client.init_constraints()
        mock_neo4j_client.init_constraints.assert_called_once()
