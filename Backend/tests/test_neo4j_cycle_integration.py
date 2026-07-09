"""
Real-Neo4j integration tests for the cycle-detection query path.

The unit tests in test_cycle_detection.py mock find_cycles / upsert_transaction_graph,
so they never exercise the actual FLOWS_TO Cypher, the rotation-invariant temporal
filter, the conservation modes, or the query timeout. These tests write real edges to a
running Neo4j and assert the query behaves correctly end to end.

They connect to the Neo4j configured via NEO4J_URI / NEO4J_PASSWORD (the docker-compose
instance). If no Neo4j is reachable, the whole module is skipped — so this is safe in a
bare CI environment but runs locally against `docker compose up neo4j`.

Isolation: every account id is prefixed `ITEST_` and cleaned up before and after each
test, so these tests never touch benchmark or production data in the same database.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from db.neo4j import Neo4jClient, NEO4J_DATABASE

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_PREFIX = "ITEST_"
_BASE = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)


async def _reachable(client: Neo4jClient) -> bool:
    try:
        await client.initialize()
        async with client.driver.session(database=NEO4J_DATABASE) as s:
            await s.run("RETURN 1")
        return True
    except Exception:
        return False


async def _wipe(client: Neo4jClient) -> None:
    """Delete every ITEST_ account and its edges (both TRANSFER and FLOWS_TO)."""
    async with client.driver.session(database=NEO4J_DATABASE) as s:
        await s.run(
            "MATCH (a:Account) WHERE a.id STARTS WITH $p DETACH DELETE a",
            p=_PREFIX,
        )


@pytest_asyncio.fixture
async def neo4j():
    """A live Neo4jClient with a clean ITEST_ namespace, or skip if unreachable."""
    client = Neo4jClient()
    if not await _reachable(client):
        await client.close()
        pytest.skip("No Neo4j reachable at NEO4J_URI — start `docker compose up neo4j`")
    await client.init_constraints()
    await _wipe(client)
    try:
        yield client
    finally:
        await _wipe(client)
        await client.close()


async def _write(client: Neo4jClient, src, dst, amount_cents, minutes, rail="USD", txn=None):
    """Write one TRANSFER (and its FLOWS_TO aggregate) between ITEST_ accounts."""
    txn = txn or f"{_PREFIX}txn_{src}_{dst}_{minutes}_{amount_cents}"
    await client.upsert_transaction_graph(
        sender_id=f"{_PREFIX}{src}",
        receiver_id=f"{_PREFIX}{dst}",
        amount_cents=amount_cents,
        timestamp_utc=_BASE + timedelta(minutes=minutes),
        rail=rail,
        event_type="SETTLEMENT",
        transaction_id=txn,
        idempotency_key=txn,
    )


def _ref():
    # Reference time just after the test transactions, so a wide window covers them.
    return _BASE + timedelta(hours=1)


# --------------------------------------------------------------------------- #
# Topology + detection
# --------------------------------------------------------------------------- #

async def test_simple_ring_is_detected(neo4j):
    """A→B→C→A with forward time and conserved amounts is found."""
    await _write(neo4j, "A", "B", 100_000, minutes=0)
    await _write(neo4j, "B", "C", 95_000, minutes=10)
    await _write(neo4j, "C", "A", 92_000, minutes=20)

    cycles = await neo4j.find_cycles(
        f"{_PREFIX}A", max_depth=6, window_hours=48,
        max_hop_gap_hours=24.0, reference_time=_ref(), conservation_mode="hop",
    )
    assert len(cycles) >= 1
    ring = cycles[0]["node_ids"]
    ids = {n.replace(_PREFIX, "") for n in ring}
    assert {"A", "B", "C"}.issubset(ids)


async def test_acyclic_chain_is_not_detected(neo4j):
    """A→B→C with no return edge produces no cycle."""
    await _write(neo4j, "A", "B", 100_000, minutes=0)
    await _write(neo4j, "B", "C", 95_000, minutes=10)

    cycles = await neo4j.find_cycles(
        f"{_PREFIX}A", reference_time=_ref(), conservation_mode="hop",
    )
    assert cycles == []


async def test_parallel_edges_collapse_to_one_ring(neo4j):
    """
    Multiple TRANSFER edges between the same pair must not explode the search.
    The FLOWS_TO aggregate collapses them; the ring is still found exactly once
    (after fingerprint dedup the ring exists, tx_count reflects the parallelism).
    """
    # Two parallel A→B transactions
    await _write(neo4j, "A", "B", 100_000, minutes=0, txn=f"{_PREFIX}ab1")
    await _write(neo4j, "A", "B", 98_000, minutes=5, txn=f"{_PREFIX}ab2")
    await _write(neo4j, "B", "C", 95_000, minutes=10)
    await _write(neo4j, "C", "A", 92_000, minutes=20)

    async with neo4j.driver.session(database=NEO4J_DATABASE) as s:
        rec = await (await s.run(
            "MATCH (:Account {id:$a})-[f:FLOWS_TO]->(:Account {id:$b}) RETURN f.tx_count AS c",
            a=f"{_PREFIX}A", b=f"{_PREFIX}B",
        )).single()
    assert rec["c"] == 2  # aggregate counts both parallel txns

    cycles = await neo4j.find_cycles(
        f"{_PREFIX}A", reference_time=_ref(), conservation_mode="hop",
    )
    assert len(cycles) >= 1


# --------------------------------------------------------------------------- #
# Temporal filter
# --------------------------------------------------------------------------- #

async def test_outside_window_is_excluded(neo4j):
    """A ring entirely older than the window is not returned."""
    await _write(neo4j, "A", "B", 100_000, minutes=0)
    await _write(neo4j, "B", "C", 95_000, minutes=10)
    await _write(neo4j, "C", "A", 92_000, minutes=20)

    # Reference time 100 days later with a 48h window → ring falls outside.
    far_ref = _BASE + timedelta(days=100)
    cycles = await neo4j.find_cycles(
        f"{_PREFIX}A", window_hours=48, reference_time=far_ref, conservation_mode="hop",
    )
    assert cycles == []


async def test_hop_gap_too_large_is_excluded(neo4j):
    """A ring whose hops are spread beyond max_hop_gap is rejected."""
    await _write(neo4j, "A", "B", 100_000, minutes=0)
    await _write(neo4j, "B", "C", 95_000, minutes=10)
    # Last hop is 10 days after the previous one.
    await _write(neo4j, "C", "A", 92_000, minutes=10 + 10 * 24 * 60)

    cycles = await neo4j.find_cycles(
        f"{_PREFIX}A", window_hours=24 * 365, max_hop_gap_hours=24.0,
        reference_time=_BASE + timedelta(days=20), conservation_mode="hop",
    )
    assert cycles == []


# --------------------------------------------------------------------------- #
# Conservation modes
# --------------------------------------------------------------------------- #

async def test_conservation_hop_rejects_growing_amounts(neo4j):
    """Money that grows around the ring fails hop conservation but passes 'off'."""
    # Amounts grow: 100k → 200k → 300k (money can't grow in a real flow)
    await _write(neo4j, "A", "B", 100_000, minutes=0)
    await _write(neo4j, "B", "C", 200_000, minutes=10)
    await _write(neo4j, "C", "A", 300_000, minutes=20)

    hop = await neo4j.find_cycles(
        f"{_PREFIX}A", reference_time=_ref(), conservation_mode="hop",
    )
    off = await neo4j.find_cycles(
        f"{_PREFIX}A", reference_time=_ref(), conservation_mode="off",
    )
    assert hop == []          # conservation rejects a growing "flow"
    assert len(off) >= 1      # topology + temporal alone still see the ring


async def test_cross_currency_skips_conservation(neo4j):
    """
    Different currencies (rail) on consecutive hops skip the conservation check,
    so a cross-currency ring is still detected even though raw cents differ wildly.
    """
    await _write(neo4j, "A", "B", 100_000, minutes=0, rail="USD")
    await _write(neo4j, "B", "C", 700_000, minutes=10, rail="CNY")  # ~7x cents, different ccy
    await _write(neo4j, "C", "A", 90_000, minutes=20, rail="USD")

    cycles = await neo4j.find_cycles(
        f"{_PREFIX}A", reference_time=_ref(), conservation_mode="hop",
    )
    assert len(cycles) >= 1


# --------------------------------------------------------------------------- #
# Value floor + timeout
# --------------------------------------------------------------------------- #

async def test_value_floor_excludes_trivial_ring(neo4j):
    """A ring whose weakest hop is below the floor is ignored."""
    await _write(neo4j, "A", "B", 100_000, minutes=0)
    await _write(neo4j, "B", "C", 500, minutes=10)   # $5 — below a $100 floor
    await _write(neo4j, "C", "A", 400, minutes=20)

    cycles = await neo4j.find_cycles(
        f"{_PREFIX}A", min_cycle_cents=10_000, reference_time=_ref(),
        conservation_mode="off",
    )
    assert cycles == []


async def test_query_timeout_param_is_accepted(neo4j):
    """
    The query_timeout_seconds param is wired through and the normal path returns a
    valid list. (The degradation-to-[] on a real timeout is exercised by the benchmark,
    where deep searches on high-degree accounts genuinely exceed the budget; a 3-node
    ring completes faster than Neo4j's timeout-check interval, so it can't be forced
    to time out here.)
    """
    await _write(neo4j, "A", "B", 100_000, minutes=0)
    await _write(neo4j, "B", "C", 95_000, minutes=10)
    await _write(neo4j, "C", "A", 92_000, minutes=20)

    result = await neo4j.find_cycles(
        f"{_PREFIX}A", reference_time=_ref(), query_timeout_seconds=5.0,
        conservation_mode="hop",
    )
    assert isinstance(result, list)
    assert len(result) >= 1
