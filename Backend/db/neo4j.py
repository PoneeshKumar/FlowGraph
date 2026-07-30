"""
Neo4j client for FlowGraph.

Handles property graph operations: creating/updating Account nodes and TRANSFER edges.
Each payment becomes a distinct edge keyed by txn_id (MERGE-safe for outbox retries).

Edge model:
    (src:Account)-[:TRANSFER {txn_id, amount_cents, ts, rail, event_type}]->(dst:Account)

  - One edge per payment (not one aggregated edge per pair). This is required for
    transaction-level cycle detection (tracing a specific flow of funds around a loop).
  - ts is unix SECONDS (int) for fast comparisons in cycle Cypher.
  - Volume/window aggregation lives in Redis, not here.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from neo4j import AsyncGraphDatabase, AsyncDriver, AsyncSession, Query
from neo4j.exceptions import ServiceUnavailable

from algorithms.pagerank import compute_weighted_pagerank

from config import (
    NEO4J_URI,
    NEO4J_USER,
    NEO4J_PASSWORD,
    NEO4J_DATABASE,
    CYCLE_MAX_DEPTH,
    CYCLE_WINDOW_HOURS,
    CYCLE_MAX_HOP_GAP_HOURS,
    CYCLE_MAX_LEAK,
    CYCLE_MIN_VALUE_CENTS,
    CYCLE_MAX_RESULTS,
    CYCLE_CONSERVATION_MODE,
    CYCLE_MAX_CYCLE_LEAK,
    CYCLE_QUERY_TIMEOUT_SECONDS,
    LOUVAIN_WINDOW_DAYS,
    LOUVAIN_EXPORT_TIMEOUT_SECONDS,
    LOUVAIN_ASSIGN_BATCH_SIZE,
)

logger = logging.getLogger(__name__)


class Neo4jClient:
    """Async Neo4j client for graph operations."""

    def __init__(self):
        self.driver: Optional[AsyncDriver] = None

    async def initialize(self) -> None:
        """Initialize Neo4j driver and test connection."""
        try:
            self.driver = AsyncGraphDatabase.driver(
                NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD), encrypted=False
            )
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                await session.run("RETURN 1")
            logger.info("Neo4j driver initialized and connection verified")
        except Exception as e:
            logger.error(f"Failed to initialize Neo4j driver: {e}")
            raise

    async def close(self) -> None:
        """Close Neo4j driver."""
        if self.driver:
            await self.driver.close()
            logger.info("Neo4j driver closed")

    async def init_constraints(self) -> None:
        """
        Create uniqueness constraints for Account nodes and TRANSFER edges.

        Called once at startup (safe to re-run — IF NOT EXISTS).
        Relationship property uniqueness requires Neo4j 5.7+; a warning is logged
        if unavailable. The MERGE on txn_id still prevents duplicates either way.
        """
        constraints = [
            "CREATE CONSTRAINT account_id_unique IF NOT EXISTS "
            "FOR (a:Account) REQUIRE a.id IS UNIQUE",
            "CREATE CONSTRAINT transfer_txn_unique IF NOT EXISTS "
            "FOR ()-[t:TRANSFER]-() REQUIRE t.txn_id IS UNIQUE",
        ]
        async with self.driver.session(database=NEO4J_DATABASE) as session:
            for stmt in constraints:
                try:
                    await session.run(stmt)
                except Exception as e:
                    # Relationship-property uniqueness constraint may not be available
                    # on Neo4j < 5.7. Log a warning rather than crashing startup.
                    logger.warning(
                        "Could not create constraint (may require Neo4j 5.7+): %s | %s",
                        stmt,
                        e,
                    )
        logger.info("Neo4j constraints initialized")

    async def upsert_transaction_graph(
        self,
        sender_id: str,
        receiver_id: str,
        amount_cents: int,
        timestamp_utc: datetime,
        rail: str,
        event_type: str,
        transaction_id: str,
        idempotency_key: str,
    ) -> None:
        """
        Idempotent upsert of a payment as a per-transaction TRANSFER edge.

        Graph model:
            MERGE (src:Account {id: sender_id})
            MERGE (dst:Account {id: receiver_id})
            MERGE (src)-[:TRANSFER {txn_id: transaction_id}]->(dst)

        MERGE on txn_id means outbox retries are safe — the ON CREATE block only
        fires once per transaction_id. Account nodes are created on first appearance.

        Timestamps are stored as unix seconds (int) for fast integer comparisons in
        the cycle-detection Cypher. Neo4j's timestamp() returns ms; we use explicit
        epoch seconds to avoid the ms/s mixing bug in the previous stub.

        Args:
            sender_id: Hashed sender identifier
            receiver_id: Hashed receiver identifier
            amount_cents: Amount in cents
            timestamp_utc: UTC timestamp of transaction
            rail: Payment rail (CARD, WIRE, ACH, CRYPTO)
            event_type: Event type (AUTH, SETTLEMENT, etc.)
            transaction_id: Unique transaction ID — used as TRANSFER.txn_id (MERGE key)
            idempotency_key: Kept for interface compatibility; MERGE on txn_id is the guard
        """
        # Two edges are maintained per payment:
        #   1. TRANSFER — one per transaction (MERGE on txn_id), full per-txn detail.
        #      Used for audit and any per-txn tracing.
        #   2. FLOWS_TO — one aggregate edge per directed account pair (CLAUDE.md data
        #      model: total_amount, tx_count, first_seen, last_seen, avg_tx_size).
        #      Cycle detection runs on FLOWS_TO because it collapses the 20-50 parallel
        #      TRANSFER edges between a pair into a single edge, so variable-length cycle
        #      search does not explode combinatorially (branching = distinct neighbours,
        #      not parallel-edge count).
        #
        # Idempotency: TRANSFER is safe on retry (MERGE on txn_id). FLOWS_TO aggregates
        # are incremented on every call; the outbox's once-delivery guarantee prevents
        # double-counting in production.
        query = """
        MERGE (src:Account {id: $sender_id})
          ON CREATE SET src.created_at = timestamp()
        MERGE (dst:Account {id: $receiver_id})
          ON CREATE SET dst.created_at = timestamp()
        MERGE (src)-[t:TRANSFER {txn_id: $transaction_id}]->(dst)
          ON CREATE SET
            t.amount_cents = $amount_cents,
            t.ts           = $timestamp_utc,
            t.rail         = $rail,
            t.event_type   = $event_type,
            t.created_at   = timestamp()
        MERGE (src)-[f:FLOWS_TO]->(dst)
          ON CREATE SET
            f.tx_count     = 1,
            f.total_amount = $amount_cents,
            f.first_ts     = $timestamp_utc,
            f.last_ts      = $timestamp_utc,
            f.min_amount   = $amount_cents,
            f.max_amount   = $amount_cents,
            f.rail         = $rail
          ON MATCH SET
            f.tx_count     = f.tx_count + 1,
            f.total_amount = f.total_amount + $amount_cents,
            f.first_ts     = CASE WHEN $timestamp_utc < f.first_ts THEN $timestamp_utc ELSE f.first_ts END,
            f.last_ts      = CASE WHEN $timestamp_utc > f.last_ts  THEN $timestamp_utc ELSE f.last_ts END,
            f.min_amount   = CASE WHEN $amount_cents < f.min_amount THEN $amount_cents ELSE f.min_amount END,
            f.max_amount   = CASE WHEN $amount_cents > f.max_amount THEN $amount_cents ELSE f.max_amount END
        """

        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                await session.run(
                    query,
                    sender_id=sender_id,
                    receiver_id=receiver_id,
                    amount_cents=amount_cents,
                    timestamp_utc=int(timestamp_utc.timestamp()),  # unix seconds
                    rail=rail,
                    event_type=event_type,
                    transaction_id=transaction_id,
                )
            logger.debug(
                "Graph upserted: %s → %s | txn=%s | %d cents",
                sender_id,
                receiver_id,
                transaction_id,
                amount_cents,
            )

            await self.compute_local_pagerank([sender_id, receiver_id])
        except ServiceUnavailable as e:
            logger.error(f"Neo4j temporarily unavailable: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to upsert transaction graph: {e}")
            raise

    async def find_cycles(
        self,
        account_id: str,
        max_depth: int = CYCLE_MAX_DEPTH,
        window_hours: int = CYCLE_WINDOW_HOURS,
        max_hop_gap_hours: float = CYCLE_MAX_HOP_GAP_HOURS,
        max_leak: float = CYCLE_MAX_LEAK,
        min_cycle_cents: int = CYCLE_MIN_VALUE_CENTS,
        max_results: int = CYCLE_MAX_RESULTS,
        reference_time: Optional[datetime] = None,
        conservation_mode: str = CYCLE_CONSERVATION_MODE,
        max_cycle_leak: float = CYCLE_MAX_CYCLE_LEAK,
        query_timeout_seconds: float = CYCLE_QUERY_TIMEOUT_SECONDS,
    ) -> List[Dict[str, Any]]:
        """
        Find transaction-level circular flows starting from a given account.

        Returns only TRACED FLOWS where:
        - All hops are within `window_hours` of now
        - Timestamps are monotonically non-decreasing hop-to-hop (money moves forward)
        - Consecutive-hop gap <= max_hop_gap_hours (same flow, not coincidental loop)
        - Amount at each hop is <= previous hop and >= previous * (1 - max_leak)
          (money can bleed off via fees but cannot grow; tight conservation → higher score)
        - The weakest hop clears min_cycle_cents (ignores trivial test noise)

        Implementation note on `max_depth`:
        Cypher requires a literal integer in variable-length path patterns (*2..N).
        max_depth is a config-sourced int clamped to [2, 6] and formatted into the
        query via an f-string. It is NOT user-supplied data (no injection risk) — all
        runtime values (account_id, timestamps, thresholds) are named parameters.

        Args:
            account_id: Starting account
            max_depth: Max hops in cycle, clamped to [2, 6]
            window_hours: Look back this many hours
            max_hop_gap_hours: Max hours allowed between consecutive hops
            max_leak: Max fractional bleed-off per hop (e.g. 0.20 = 20%)
            min_cycle_cents: Minimum weakest-hop amount to flag
            max_results: Cap on number of returned cycles

        Returns:
            List of dicts: {node_ids, amounts, timestamps, txn_ids}
        """
        max_depth = max(2, min(max_depth, max_depth))  # allow config-controlled depth

        ref = reference_time if reference_time is not None else datetime.now(timezone.utc)
        now_epoch = int(ref.timestamp())
        window_start_epoch = now_epoch - int(window_hours * 3600)
        max_hop_gap_seconds = int(max_hop_gap_hours * 3600)

        # Cycle search runs on the aggregated FLOWS_TO edge (one per directed pair),
        # NOT the per-txn TRANSFER edge. This is essential for performance: real payment
        # data has 20-50 parallel TRANSFER edges between the same pair, which makes a
        # variable-length TRANSFER search O(parallel^depth) — billions of paths. FLOWS_TO
        # collapses each pair to a single edge, so branching is the number of distinct
        # neighbours (tiny in a laundering ring).
        #
        # Temporal feasibility on aggregate envelopes:
        #   - last_ts[i+1] >= first_ts[i]  → a forward-ordered txn pair exists
        #   - first_ts[i+1] - last_ts[i] <= max_gap → hops are close enough to be one flow
        # Conservation is currency-aware: when consecutive hops use different currencies
        # (rail differs), raw cent comparison is meaningless across FX, so we skip it.
        # Otherwise the representative (max) amount must be non-growing within max_leak.
        # Temporal model is rotation-invariant. A cycle seeded from an arbitrary account
        # crosses one "wrap point" where time resets (the flow's start node), so requiring
        # strictly increasing timestamps along the path is wrong — it fails for every
        # rotation except one. Instead we require AT MOST ONE time-descent around the ring:
        # a genuine one-directional money flow ascends on every hop except the single wrap,
        # while a coincidental topological ring has many descents.
        #
        # `ft` is the per-hop representative time (first_ts). Index i+1 wraps to 0 so the
        # descent count is computed over the full circular sequence.
        #
        # Conservation has three modes (config CYCLE_CONSERVATION_MODE):
        #   "hop"   — per-hop range-overlap on the aggregate min/max envelope: consecutive
        #             same-currency hops must have overlapping amount ranges within max_leak
        #             (some pair of txns across the two hops forms a conserved step).
        #             Cross-currency hops skip it (FX makes cents incomparable).
        #   "cycle" — whole-ring magnitude consistency: the weakest hop's representative
        #             amount is >= the strongest hop's × (1 - max_cycle_leak). Captures the
        #             laundering signal (money returns to origin at similar magnitude) while
        #             tolerating per-hop fee/split variation. Default.
        #   "off"   — no amount conservation; topology + temporal + value floor only.
        conservation_mode = (conservation_mode or "cycle").lower()
        if conservation_mode == "hop":
            conservation_clause = """
          AND ALL(i IN range(0, n-2) WHERE
                rels[i].rail <> rels[i+1].rail
                OR (
                  rels[i+1].min_amount <= rels[i].max_amount
                  AND rels[i+1].max_amount >= toInteger(rels[i].min_amount * (1.0 - $max_leak))
                ))"""
        elif conservation_mode == "off":
            conservation_clause = ""
        else:  # "cycle" (default)
            conservation_clause = """
          AND reduce(mx = rels[0].max_amount, r IN rels |
                CASE WHEN r.max_amount > mx THEN r.max_amount ELSE mx END)
              * (1.0 - $max_cycle_leak)
              <= reduce(mn = rels[0].max_amount, r IN rels |
                CASE WHEN r.max_amount < mn THEN r.max_amount ELSE mn END)"""

        query = f"""
        MATCH path = (start:Account {{id: $account_id}})-[rels:FLOWS_TO*2..{max_depth}]->(start)
        WITH path, rels, [r IN rels | r.first_ts] AS ft, size(rels) AS n
        WHERE ALL(r IN rels WHERE r.last_ts >= $window_start_epoch)
          AND size([i IN range(0, n-1) WHERE
                (CASE WHEN i+1 = n THEN ft[0] ELSE ft[i+1] END) < ft[i]]) <= 1
          AND ALL(i IN range(0, n-1) WHERE
                (CASE WHEN i+1 = n THEN ft[0] ELSE ft[i+1] END) < ft[i]
                OR (CASE WHEN i+1 = n THEN ft[0] ELSE ft[i+1] END) - ft[i] <= $max_hop_gap_seconds){conservation_clause}
          AND reduce(mn = rels[0].max_amount, r IN rels |
                CASE WHEN r.max_amount < mn THEN r.max_amount ELSE mn END) >= $min_cycle_cents
        RETURN [nd IN nodes(path) | nd.id] AS node_ids,
               [r IN rels | r.max_amount]  AS amounts,
               [r IN rels | r.last_ts]     AS timestamps,
               [r IN rels | r.tx_count]    AS txn_ids
        LIMIT $max_results
        """

        # Transaction timeout bounds the worst case: a deep fruitless search on a
        # high-degree account must never stall the pipeline. On timeout Neo4j raises,
        # which we treat as "no cycle from this account" — correct, since a search that
        # can't finish in the budget yields no actionable flag.
        timed_query = Query(query, timeout=query_timeout_seconds)

        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                result = await session.run(
                    timed_query,
                    account_id=account_id,
                    window_start_epoch=window_start_epoch,
                    max_hop_gap_seconds=max_hop_gap_seconds,
                    max_leak=max_leak,
                    max_cycle_leak=max_cycle_leak,
                    min_cycle_cents=min_cycle_cents,
                    max_results=max_results,
                )
                records = await result.fetch(max_results)
                return [
                    {
                        "node_ids":   record["node_ids"],
                        "amounts":    record["amounts"],
                        "timestamps": record["timestamps"],
                        "txn_ids":    record["txn_ids"],
                    }
                    for record in records
                ]
        except Exception as e:
            # ClientError with code TransactionTimeoutClientError (or the driver's
            # timeout) means the search exceeded its budget — treat as no cycle found
            # rather than failing the whole detection pass.
            if "timeout" in str(e).lower() or "Timeout" in type(e).__name__:
                logger.warning(
                    "Cycle query for %s exceeded %.1fs budget — treating as no cycle",
                    account_id[:8], query_timeout_seconds,
                )
                return []
            logger.error(f"Failed to find cycles for {account_id}: {e}")
            raise

    async def export_flows_to_edges(
        self,
        window_days: int = LOUVAIN_WINDOW_DAYS,
        reference_time: Optional[datetime] = None,
        query_timeout_seconds: float = LOUVAIN_EXPORT_TIMEOUT_SECONDS,
    ) -> List[Dict[str, Any]]:
        """
        Export the aggregate FLOWS_TO edge list for community detection.

        Returns one record per directed account pair whose relationship was
        active (last_ts) within the window. The Louvain batch collapses the
        two directions Python-side (build_undirected_graph); exporting raw
        directed records keeps this query a trivial scan.

        Args:
            window_days:    Only edges with last_ts within this many days
            reference_time: Window anchor; defaults to now (benchmarks anchor
                            to the dataset's own max timestamp instead)

        Returns:
            List of dicts: {src, dst, total_amount, tx_count}
        """
        ref = reference_time if reference_time is not None else datetime.now(timezone.utc)
        window_start_epoch = int(ref.timestamp()) - window_days * 86400

        query = """
        MATCH (a:Account)-[f:FLOWS_TO]->(b:Account)
        WHERE f.last_ts >= $window_start_epoch
        RETURN a.id AS src, b.id AS dst,
               f.total_amount AS total_amount, f.tx_count AS tx_count
        """
        # Batch export may legitimately take longer than the per-account cycle
        # budget, but must still be bounded — an unindexed runaway scan cannot
        # be allowed to hang the batch forever.
        timed_query = Query(query, timeout=query_timeout_seconds)

        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                result = await session.run(
                    timed_query, window_start_epoch=window_start_epoch
                )
                return [
                    {
                        "src":          record["src"],
                        "dst":          record["dst"],
                        "total_amount": record["total_amount"],
                        "tx_count":     record["tx_count"],
                    }
                    async for record in result
                ]
        except Exception as e:
            logger.error(f"Failed to export FLOWS_TO edges: {e}")
            raise

    async def bulk_upsert_transactions(
        self,
        transactions: Sequence[Dict[str, Any]],
        batch_size: int = 1000,
    ) -> int:
        """
        Batched UNWIND write of many transactions — for dataset ingestion only.

        The streaming path (upsert_transaction_graph) issues one Cypher round
        trip per payment AND recomputes local PageRank per payment, which is
        roughly three round trips each. That is correct for a live stream and
        hopeless for bulk: 5M rows would be ~15M queries. This writes
        batch_size rows per round trip and skips PageRank entirely — call
        recompute_pagerank_full() once after the load instead.

        Graph semantics are identical to upsert_transaction_graph: TRANSFER
        MERGEd on txn_id, FLOWS_TO MERGEd on the account pair with aggregates
        incremented. UNWIND rows are applied in order within one transaction and
        MERGE sees earlier writes from the same transaction, so repeated account
        pairs inside a batch accumulate correctly rather than overwriting.

        Rows sharing a transaction_id are deduplicated within each batch.
        txn_id is the declared idempotency key, so two rows carrying the same
        one are the same payment: TRANSFER would MERGE to a single edge anyway,
        while FLOWS_TO aggregates would double-count without this. Dedup is
        per-batch only — a repeat that straddles two batches still inflates the
        aggregates, as it would on the streaming path.

        Args:
            transactions: Dicts with sender_id, receiver_id, amount_cents,
                          timestamp_utc (datetime or unix seconds), rail,
                          event_type, transaction_id.
            batch_size:   Rows per round trip.

        Returns:
            Count of rows actually written after dedup.
        """
        if not self.driver:
            logger.debug("Neo4j driver not initialized; skipping bulk upsert")
            return 0
        if not transactions:
            return 0
        if batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {batch_size}")

        query = """
        UNWIND $rows AS row
        MERGE (src:Account {id: row.sender_id})
          ON CREATE SET src.created_at = timestamp()
        MERGE (dst:Account {id: row.receiver_id})
          ON CREATE SET dst.created_at = timestamp()
        MERGE (src)-[t:TRANSFER {txn_id: row.transaction_id}]->(dst)
          ON CREATE SET
            t.amount_cents = row.amount_cents,
            t.ts           = row.timestamp_utc,
            t.rail         = row.rail,
            t.event_type   = row.event_type,
            t.created_at   = timestamp()
        MERGE (src)-[f:FLOWS_TO]->(dst)
          ON CREATE SET
            f.tx_count     = 1,
            f.total_amount = row.amount_cents,
            f.first_ts     = row.timestamp_utc,
            f.last_ts      = row.timestamp_utc,
            f.min_amount   = row.amount_cents,
            f.max_amount   = row.amount_cents,
            f.rail         = row.rail
          ON MATCH SET
            f.tx_count     = f.tx_count + 1,
            f.total_amount = f.total_amount + row.amount_cents,
            f.first_ts     = CASE WHEN row.timestamp_utc < f.first_ts THEN row.timestamp_utc ELSE f.first_ts END,
            f.last_ts      = CASE WHEN row.timestamp_utc > f.last_ts  THEN row.timestamp_utc ELSE f.last_ts END,
            f.min_amount   = CASE WHEN row.amount_cents < f.min_amount THEN row.amount_cents ELSE f.min_amount END,
            f.max_amount   = CASE WHEN row.amount_cents > f.max_amount THEN row.amount_cents ELSE f.max_amount END
        """

        written = 0
        for start in range(0, len(transactions), batch_size):
            chunk = transactions[start : start + batch_size]

            seen: set = set()
            rows: List[Dict[str, Any]] = []
            for txn in chunk:
                txn_id = txn["transaction_id"]
                if txn_id in seen:
                    continue
                seen.add(txn_id)

                timestamp = txn["timestamp_utc"]
                if isinstance(timestamp, datetime):
                    timestamp = int(timestamp.timestamp())

                rows.append(
                    {
                        "sender_id": txn["sender_id"],
                        "receiver_id": txn["receiver_id"],
                        "amount_cents": int(txn["amount_cents"]),
                        "timestamp_utc": int(timestamp),
                        "rail": txn["rail"],
                        "event_type": txn["event_type"],
                        "transaction_id": txn_id,
                    }
                )

            if not rows:
                continue

            try:
                async with self.driver.session(database=NEO4J_DATABASE) as session:
                    await session.run(query, rows=rows)
                written += len(rows)
            except Exception as e:
                logger.error(
                    f"Bulk upsert failed on batch at offset {start}: {e}"
                )
                raise

        logger.info(f"Bulk upsert wrote {written} transactions")
        return written

    async def recompute_pagerank_full(
        self,
        window_days: int = 365,
        reference_time: Optional[datetime] = None,
        write_batch_size: int = 10_000,
        damping: float = 0.85,
        max_iterations: int = 100,
        export_timeout_seconds: float = 1800.0,
    ) -> int:
        """
        Recompute PageRank across the whole graph and persist every score.

        The counterpart to bulk_upsert_transactions, which skips the
        per-transaction PageRank the streaming path does. Run this once after a
        dataset load.

        Uses compute_pagerank_sparse, not compute_weighted_pagerank: the latter
        allocates a dense node_count^2 matrix and cannot survive a real graph
        (515k accounts would need ~2.1 PB).

        Args:
            window_days:      Only FLOWS_TO edges active within this window.
                              Defaults to a year so a whole dataset is covered,
                              unlike the Louvain default.
            reference_time:   Window anchor; defaults to now. Historical
                              datasets should anchor to their own max ts, or
                              every edge looks stale.
            write_batch_size: Accounts per score-write round trip.
            export_timeout_seconds:
                              Timeout for the edge export. Must be far larger
                              than LOUVAIN_EXPORT_TIMEOUT_SECONDS (120s), which
                              export_flows_to_edges defaults to: that budget is
                              sized for the Louvain window, and a whole-graph
                              export blows straight through it. Observed on the
                              HI-Small load — 1,010,384 FLOWS_TO edges timed out
                              at 120s and killed the run after the data had
                              already been written.

        Returns:
            Number of accounts whose score was written.
        """
        if not self.driver:
            logger.debug("Neo4j driver not initialized; skipping PageRank recompute")
            return 0

        from algorithms.pagerank import compute_pagerank_sparse

        edges = await self.export_flows_to_edges(
            window_days=window_days,
            reference_time=reference_time,
            query_timeout_seconds=export_timeout_seconds,
        )
        if not edges:
            logger.warning("No FLOWS_TO edges in window; nothing to score")
            return 0

        scores = compute_pagerank_sparse(
            (
                (e["src"], e["dst"], float(e.get("total_amount") or 0.0))
                for e in edges
            ),
            damping=damping,
            max_iterations=max_iterations,
        )
        if not scores:
            return 0

        update_query = """
        UNWIND $updates AS update
        MATCH (n:Account {id: update.account_id})
        SET n.pagerank_score = update.score,
            n.pagerank_updated_at = timestamp()
        """

        updates = [
            {"account_id": account_id, "score": round(score, 10)}
            for account_id, score in sorted(scores.items())
        ]

        written = 0
        for start in range(0, len(updates), write_batch_size):
            chunk = updates[start : start + write_batch_size]
            try:
                async with self.driver.session(database=NEO4J_DATABASE) as session:
                    await session.run(update_query, updates=chunk)
                written += len(chunk)
            except Exception as e:
                logger.error(f"PageRank write failed at offset {start}: {e}")
                raise

        logger.info(
            f"PageRank recomputed over {len(edges)} edges, "
            f"{written} account scores written"
        )
        return written

    async def get_flows_to_timestamp(
        self, percentile: float = 1.0
    ) -> Optional[int]:
        """
        A FLOWS_TO.last_ts quantile, as unix seconds — for anchoring time windows.

        Needed because the data is historical. IBM AML is from 2022, so anchoring
        the Redis 1h/24h/7d windows at wall-clock now makes every ZRANGEBYSCORE
        match nothing and silently zeroes 12 of the 29 feature columns.

        percentile defaults below 1.0 for a reason. The absolute max is often a
        near-empty tail: measured on HI-Small, 2022-09-01..09-10 holds 5,076,237
        transactions and 09-11..09-18 holds 1,108. Anchoring at the max (09-18)
        put only 533 of 513,987 accounts inside the 7-day window. A high
        quantile lands where the data actually is.

        A dedicated aggregation rather than reading it off export_flows_to_edges,
        which returns only src/dst/total_amount/tx_count — and which would mean
        streaming a million rows to learn one number.

        Args:
            percentile: 1.0 for the true max, or a quantile in (0, 1) — 0.999
                        skips a sparse tail without moving materially into the
                        bulk of the data.

        Returns:
            Unix seconds, or None on an empty graph.
        """
        if not self.driver:
            return None
        if not 0.0 < percentile <= 1.0:
            raise ValueError(f"percentile must be in (0, 1], got {percentile}")

        if percentile >= 1.0:
            query = """
            MATCH ()-[f:FLOWS_TO]->()
            RETURN max(f.last_ts) AS max_ts
            """
        else:
            query = """
            MATCH ()-[f:FLOWS_TO]->()
            RETURN percentileDisc(f.last_ts, $percentile) AS max_ts
            """
        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                if percentile >= 1.0:
                    result = await session.run(query)
                else:
                    result = await session.run(query, percentile=percentile)
                record = await result.single()
        except Exception as e:
            logger.error(f"Failed to read FLOWS_TO timestamp quantile: {e}")
            raise

        if not record or record["max_ts"] is None:
            return None
        return int(record["max_ts"])

    async def export_account_nodes(
        self,
        query_timeout_seconds: float = LOUVAIN_EXPORT_TIMEOUT_SECONDS,
    ) -> List[Dict[str, Any]]:
        """
        Export every graph node with the properties the GNN features read.

        Companion to export_flows_to_edges: that gives the edges, this gives
        the nodes. Together they are everything the feature builder needs from
        Neo4j.

        `labels` comes back so node type can be one-hot encoded. Today the
        consumer only ever MERGEs `:Account` (see upsert_transaction_graph),
        so Merchant/Bank/Exchange will be empty until something creates them.

        kyc_tier / risk_score / country / account_age / cumulative_volume are
        returned even though nothing populates them yet — create_account_node
        is the only writer and is never called in production. They come back
        as None, the feature builder skips them, and they start contributing
        automatically once ingestion fills them in.

        Returns:
            List of dicts, one per node, keyed by property name.
        """
        query = """
        MATCH (n)
        WHERE n:Account OR n:Merchant OR n:Bank OR n:Exchange
        RETURN n.id                AS id,
               labels(n)           AS labels,
               n.pagerank_score    AS pagerank_score,
               n.community_id      AS community_id,
               n.created_at        AS created_at,
               n.kyc_tier          AS kyc_tier,
               n.risk_score        AS risk_score,
               n.country           AS country,
               n.account_age       AS account_age,
               n.cumulative_volume AS cumulative_volume
        """
        # Same reasoning as export_flows_to_edges: a full-graph scan may run
        # long but must stay bounded.
        timed_query = Query(query, timeout=query_timeout_seconds)

        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                result = await session.run(timed_query)
                return [
                    {
                        "id":                record["id"],
                        "labels":            list(record["labels"] or []),
                        "pagerank_score":    record["pagerank_score"],
                        "community_id":      record["community_id"],
                        "created_at":        record["created_at"],
                        "kyc_tier":          record["kyc_tier"],
                        "risk_score":        record["risk_score"],
                        "country":           record["country"],
                        "account_age":       record["account_age"],
                        "cumulative_volume": record["cumulative_volume"],
                    }
                    async for record in result
                    if record["id"]
                ]
        except Exception as e:
            logger.error(f"Failed to export account nodes: {e}")
            raise

    async def write_community_assignments(
        self,
        assignments: Dict[str, str],
        detected_at_epoch: int,
        batch_size: int = LOUVAIN_ASSIGN_BATCH_SIZE,
    ) -> int:
        """
        Batch-write community membership onto Account nodes.

        Sets community_id / community_detected_at as node properties. This is
        derived analytical state, NOT payment data — the outbox convention
        (Postgres first, then graph) applies to payment events; batch algorithm
        results are written directly, with detected_at recording provenance.
        MATCH (not MERGE) is deliberate: an account absent from the graph was
        deleted since export, and resurrecting it here would be wrong.

        Args:
            assignments:       account_id → community_id (12-hex-char string)
            detected_at_epoch: Run anchor time, unix seconds UTC

        Returns:
            Number of assignment rows written
        """
        if not assignments:
            return 0

        rows = [{"id": account_id, "cid": community_id}
                for account_id, community_id in assignments.items()]

        query = """
        UNWIND $rows AS row
        MATCH (a:Account {id: row.id})
        SET a.community_id = row.cid,
            a.community_detected_at = $detected_at
        """

        written = 0
        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                for i in range(0, len(rows), batch_size):
                    batch = rows[i:i + batch_size]
                    result = await session.run(
                        query, rows=batch, detected_at=detected_at_epoch
                    )
                    await result.consume()
                    written += len(batch)
            logger.info(
                "Community assignments written | accounts=%d communities=%d",
                written, len(set(assignments.values())),
            )
            return written
        except Exception as e:
            logger.error(f"Failed to write community assignments: {e}")
            raise

    async def create_account_node(
        self,
        account_id: str,
        account_type: str = "Account",
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Create or update an account node with optional properties.

        account_type must be one of the four defined node types (Account, Merchant,
        Bank, Exchange) per CLAUDE.md. Do not pass user-supplied strings here.

        Args:
            account_id: Unique account identifier
            account_type: Node label (Account, Merchant, Bank, Exchange)
            properties: Optional additional properties (risk_score, kyc_tier, etc.)
        """
        query = f"""
        MERGE (n:{account_type} {{id: $account_id}})
        ON CREATE SET n.created_at = timestamp()
        ON MATCH SET  n.updated_at = timestamp()
        """
        props: Dict[str, Any] = {"account_id": account_id}
        if properties:
            for key, value in properties.items():
                query += f", n.{key} = ${key}"
                props[key] = value

        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                await session.run(query, **props)
            logger.debug(f"Account node created/updated: {account_id}")
        except Exception as e:
            logger.error(f"Failed to create account node: {e}")
            raise

    async def compute_local_pagerank(
        self,
        account_ids: List[str],
        depth: int = 2,
        max_iterations: int = 30,
        tolerance: float = 1e-6,
    ) -> Dict[str, float]:
        """Compute weighted PageRank for a small local subgraph around the given accounts.

        The implementation pulls a local neighborhood from Neo4j, computes PageRank
        over the induced subgraph, and writes the scores back to the participating
        account nodes.
        """
        if not self.driver:
            logger.debug("Neo4j driver not initialized; skipping PageRank computation")
            return {}

        if not account_ids:
            return {}

        unique_account_ids = list(dict.fromkeys(account_ids))
        local_accounts = set(unique_account_ids)

        query = f"""
        MATCH (src:Account)
        WHERE src.id IN $account_ids
        MATCH (src)-[rel:FLOWS_TO]->(dst:Account)
        WHERE dst.id IN $account_ids OR dst.id IN $expanded_ids
        WITH src, dst, rel
        RETURN src.id AS source_id, dst.id AS target_id, rel.total_amount AS weight
        """

        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                result = await session.run(
                    query,
                    account_ids=unique_account_ids,
                    expanded_ids=unique_account_ids,
                )
                records = await result.data()
        except Exception as e:
            logger.error(f"Failed to fetch local graph for PageRank: {e}")
            return {}

        adjacency: Dict[str, Dict[str, float]] = {}
        for record in records:
            source_id = record.get("source_id")
            target_id = record.get("target_id")
            weight = float(record.get("weight", 0) or 0)
            if not source_id or not target_id:
                continue

            adjacency.setdefault(source_id, {})[target_id] = weight

            local_accounts.add(source_id)
            local_accounts.add(target_id)

        for account_id in unique_account_ids:
            adjacency.setdefault(account_id, {})

        if not adjacency:
            return {}

        scores = compute_weighted_pagerank(
            adjacency,
            max_iterations=max_iterations,
            tolerance=tolerance,
        )

        if not scores:
            return {}

        update_query = """
        UNWIND $updates AS update
        MATCH (n:Account {id: update.account_id})
        SET n.pagerank_score = update.score,
            n.pagerank_updated_at = timestamp()
        """

        updates = [
            {"account_id": account_id, "score": round(scores.get(account_id, 0.0), 8)}
            for account_id in sorted(local_accounts)
        ]

        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                await session.run(update_query, updates=updates)
        except Exception as e:
            logger.error(f"Failed to persist PageRank scores: {e}")
            return {}

        return {
            account_id: round(scores.get(account_id, 0.0), 8)
            for account_id in sorted(local_accounts)
        }

    async def get_subgraph(
        self,
        account_id: str,
        depth: int = 3,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Fetch a subgraph around a given account up to N hops.

        Used by the AI enrichment layer to build context for risk scoring.

        Args:
            account_id: Center account
            depth: Max hops from center (capped at 6)
            limit: Max nodes to return

        Returns:
            Dict with 'center', 'neighbors', and 'edges'
        """
        depth = min(depth, 6)
        query = f"""
        MATCH (center:Account {{id: $account_id}})
        MATCH (center)-[rels*1..{depth}]-(neighbors)
        WITH center, neighbors, rels
        LIMIT $limit
        RETURN
            collect(distinct center)    AS center_node,
            collect(distinct neighbors) AS neighbor_nodes,
            collect(distinct rels)      AS relationships
        """
        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                result = await session.run(query, account_id=account_id, limit=limit)
                record = await result.single()
                if record:
                    return {
                        "center":    record["center_node"],
                        "neighbors": record["neighbor_nodes"],
                        "edges":     record["relationships"],
                    }
                return {"center": None, "neighbors": [], "edges": []}
        except Exception as e:
            logger.error(f"Failed to fetch subgraph for {account_id}: {e}")
            raise

    async def shortest_path(
        self,
        source_id: str,
        target_id: str,
        max_depth: int = 4,
    ) -> Optional[Dict[str, Any]]:
        """
        Find shortest path between two accounts.

        Args:
            source_id: Source account
            target_id: Target account
            max_depth: Max hops to search

        Returns:
            Dict with 'path' and 'hops', or None if no path exists
        """
        query = f"""
        MATCH (source:Account {{id: $source_id}})
        MATCH (target:Account {{id: $target_id}})
        MATCH path = shortestPath((source)-[*1..{max_depth}]->(target))
        RETURN path, length(path) AS hop_count
        LIMIT 1
        """
        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                result = await session.run(
                    query, source_id=source_id, target_id=target_id
                )
                record = await result.single()
                if record:
                    return {"path": record["path"], "hops": record["hop_count"]}
                return None
        except Exception as e:
            logger.error(
                f"Failed to find shortest path {source_id} → {target_id}: {e}"
            )
            raise

    async def health_check(self) -> bool:
        """Test Neo4j connection."""
        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                await session.run("RETURN 1")
            return True
        except Exception as e:
            logger.error(f"Neo4j health check failed: {e}")
            return False
