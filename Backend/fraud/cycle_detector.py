"""
Cycle Detection Fraud Engine for FlowGraph.

Detects circular money flows (A→B→C→A) using transaction-level TRANSFER edges in Neo4j.
Each detected cycle is:
  1. Checked for simple-cycle validity (no repeated intermediate accounts)
  2. Canonically fingerprinted (ring rotation → sha256) so one loop = one alert
  3. Scored across four dimensions (value, velocity, amount consistency, hop count)
  4. Given a written risk-level explanation (regulatory requirement)
  5. Persisted into risk_flags via postgres.upsert_risk_flag (idempotent on fingerprint)

Standalone usage:
  python -m fraud.cycle_detector  [seeds a known A→B→C→A cycle and runs detection]

Not wired into OutboxSyncWorker — called explicitly by tests and the manual entrypoint.
Cycle-detection on live traffic requires an account→account rail (wire/crypto consumer)
which is a documented follow-up task.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import pathlib
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

import numpy as np

from config import (
    CYCLE_MIN_VALUE_CENTS,
    CYCLE_FAST_CLOSE_HOURS,
    CYCLE_LEVEL_MEDIUM,
    CYCLE_LEVEL_HIGH,
    CYCLE_LEVEL_CRITICAL,
)
from db.neo4j import Neo4jClient
from db.postgres import PostgresClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scoring (pure — no I/O, fully unit-testable)
# ---------------------------------------------------------------------------

def score_cycle(
    node_ids: List[str],
    amounts: List[int],
    timestamps: List[int],
    fast_close_hours: float = CYCLE_FAST_CLOSE_HOURS,
    level_medium: float = CYCLE_LEVEL_MEDIUM,
    level_high: float = CYCLE_LEVEL_HIGH,
    level_critical: float = CYCLE_LEVEL_CRITICAL,
) -> Dict[str, Any]:
    """
    Score a cycle candidate and produce a natural-language explanation.

    Args:
        node_ids:   Account IDs in path order (first == last for a closed loop)
        amounts:    Transfer amounts in cents per hop (len == number of edges)
        timestamps: Unix epoch seconds per hop (len == number of edges)
        fast_close_hours: Velocity scoring knee (loops closing faster score higher)
        level_medium/high/critical: Score thresholds for risk levels

    Returns:
        {
          risk_score:  float in [0.0, 1.0]
          risk_level:  'low' | 'medium' | 'high' | 'critical'
          explanation: str  (always non-empty — regulatory requirement)
          details: dict     (raw numbers for audit trail)
        }
    """
    if not amounts:
        raise ValueError("amounts must be non-empty")
    if not timestamps:
        raise ValueError("timestamps must be non-empty")
    if len(amounts) != len(timestamps):
        raise ValueError(
            f"amounts and timestamps must have equal length "
            f"(got {len(amounts)} amounts, {len(timestamps)} timestamps)"
        )

    n_hops = len(amounts)
    amounts_arr = np.asarray(amounts, dtype=np.float64)
    timestamps_arr = np.asarray(timestamps, dtype=np.float64)

    total_cents = int(amounts_arr.sum())
    min_hop_cents = int(amounts_arr.min())
    mean_cents = float(amounts_arr.mean())

    # Span of the loop in seconds. np.ptp (peak-to-peak = max - min) replaces
    # the separate max(...)/min(...) calls; cast back to plain int since this
    # flows into `details`, which gets json.dumps'd by postgres.upsert_risk_flag
    # (a numpy scalar there would fail to serialize).
    span_seconds = int(np.ptp(timestamps_arr))
    span_hours = span_seconds / 3600.0

    # --- 1. Value score (0.0–1.0) ---
    # Scale: $1k (floor) → 0.0, $1M+ → 1.0 (log scale). np.log1p(x) replaces
    # the manual log(x + 1) — same formula, dedicated stdlib/numpy function.
    _min_cents = max(CYCLE_MIN_VALUE_CENTS, 1)
    _max_cents = 100_000_000  # $1M cap for normalization
    value_score = float(min(
        1.0,
        np.log1p(max(min_hop_cents, _min_cents) / _min_cents)
        / np.log1p(_max_cents / _min_cents),
    ))

    # --- 2. Velocity score (0.0–1.0, higher = faster = more suspicious) ---
    fast_close_seconds = fast_close_hours * 3600
    if span_seconds <= 0:
        velocity_score = 1.0  # instantaneous — very suspicious
    else:
        # Inversely proportional to span; decays to ~0 at fast_close_hours * 3
        velocity_score = max(0.0, 1.0 - (span_seconds / (fast_close_seconds * 3)))

    # --- 3. Amount consistency score (0.0–1.0, low CV = more suspicious) ---
    if n_hops == 1 or mean_cents == 0:
        consistency_score = 1.0
    else:
        # np.std (population std, ddof=0) replaces the manual variance loop.
        cv = float(amounts_arr.std() / mean_cents)  # coefficient of variation
        # CV of 0 → score 1.0; CV of 1.0 (high spread) → score 0.0
        consistency_score = max(0.0, 1.0 - cv)

    # --- 4. Hop score (0.0–1.0) ---
    # 3–4 hops are the sweet spot for layering; 2 is obvious, 6 is noisy
    hop_score = {2: 0.5, 3: 0.8, 4: 0.9, 5: 0.75, 6: 0.6}.get(n_hops, 0.7)

    # --- Weighted composite ---
    risk_score = (
        0.30 * value_score
        + 0.35 * velocity_score
        + 0.25 * consistency_score
        + 0.10 * hop_score
    )
    risk_score = min(1.0, max(0.0, risk_score))

    # --- Map to level ---
    if risk_score >= level_critical:
        risk_level = "critical"
    elif risk_score >= level_high:
        risk_level = "high"
    elif risk_score >= level_medium:
        risk_level = "medium"
    else:
        risk_level = "low"

    # --- Explanation (always non-empty) ---
    total_dollars = total_cents / 100
    min_dollars = min_hop_cents / 100
    unique_accounts = len(set(node_ids))
    span_display = (
        f"{span_hours:.1f}h" if span_hours < 48 else f"{span_hours/24:.1f}d"
    )
    # 2 decimal places, not a rounded int: round(x) uses banker's rounding
    # (round(0.5) == 0), which would silently misreport values landing on a
    # .5 boundary after scaling to a percentage.
    consistency_pct = round(consistency_score * 100, 2)

    explanation = (
        f"Circular money flow detected across {unique_accounts} accounts "
        f"({n_hops} hops) closing within {span_display}. "
        f"Minimum hop ${min_dollars:,.2f}, total cycle volume ${total_dollars:,.2f}. "
        f"Hop amounts consistent within {consistency_pct}%. "
        f"Risk score {risk_score:.2f} ({risk_level}). "
        f"Pattern consistent with layering / fund circulation."
    )

    details = {
        "n_hops":            n_hops,
        "total_cents":       total_cents,
        "min_hop_cents":     min_hop_cents,
        "span_seconds":      span_seconds,
        "span_hours":        round(span_hours, 2),
        "value_score":       round(value_score, 4),
        "velocity_score":    round(velocity_score, 4),
        "consistency_score": round(consistency_score, 4),
        "hop_score":         round(hop_score, 4),
        "amounts":           amounts,
        "timestamps":        timestamps,
    }

    return {
        "risk_score":  round(risk_score, 4),
        "risk_level":  risk_level,
        "explanation": explanation,
        "details":     details,
    }


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------

def cycle_fingerprint(node_ids: List[str]) -> str:
    """
    Stable unique key for a cycle ring.

    Cypher returns the path starting from the account we seeded. The same ring
    [A, B, C, A] is found when we seed from A, B, and C — giving three different
    orderings. Canonical form: drop the repeated tail node, rotate to start at the
    lexicographically smallest id, join with '→', sha256.

    Args:
        node_ids: Path nodes from Cypher (first == last for a closed loop)

    Returns:
        64-char hex sha256 fingerprint
    """
    # Drop the repeated last node (A→B→C→A → [A, B, C])
    ring = node_ids[:-1] if len(node_ids) > 1 and node_ids[0] == node_ids[-1] else node_ids

    if not ring:
        raise ValueError("cycle_fingerprint: empty ring")

    # Rotate to start at the lexicographically smallest element
    min_idx = ring.index(min(ring))
    canonical = ring[min_idx:] + ring[:min_idx]

    return hashlib.sha256("→".join(canonical).encode()).hexdigest()


def is_simple_cycle(node_ids: List[str]) -> bool:
    """
    Return True if no intermediate account is repeated in the path.

    A→B→C→A is simple (all unique intermediates).
    A→B→A→B→A is not (B appears twice).

    This filters out degenerate multi-traversal paths that Cypher's variable-length
    match can return without APOC.
    """
    # node_ids includes the repeated start at the end: [A, B, C, A]
    intermediates = node_ids[1:-1]  # exclude start and repeated-end
    return len(intermediates) == len(set(intermediates))


# ---------------------------------------------------------------------------
# Detector (requires live Neo4j + Postgres connections)
# ---------------------------------------------------------------------------

class CycleDetector:
    """
    Detects circular money flows and persists scored risk flags.

    Args:
        neo4j_client:  Initialized Neo4jClient (from db.neo4j)
        postgres_client: Initialized PostgresClient (from db.postgres)
    """

    def __init__(self, neo4j_client: Any, postgres_client: Any) -> None:
        self.neo4j = neo4j_client
        self.postgres = postgres_client

    async def detect(
        self,
        account_id: str,
        reference_time: "datetime | None" = None,
        window_hours: "int | None" = None,
    ) -> List[Dict[str, Any]]:
        """
        Run cycle detection for one account and persist any flags found.

        Steps:
        1. Query Neo4j for candidate cycles via find_cycles
        2. Filter non-simple cycles (repeated intermediates) in Python
        3. Deduplicate by canonical fingerprint — same ring from multiple seeds
           produces one flag, not N
        4. Score and generate explanation for each unique ring
        5. Upsert into risk_flags (idempotent on fingerprint)

        Args:
            account_id: Account to run detection from
            reference_time: Anchor for the look-back window (defaults to now).
            window_hours: Override the look-back span. Real-time detection uses the
                default 48h; a batch/investigative sweep over a historical dataset
                passes a span covering the data's own time range (otherwise "the
                last 48 hours" excludes every edge). ``None`` keeps find_cycles'
                default (CYCLE_WINDOW_HOURS).

        Returns:
            List of persisted flag dicts (may be empty if no cycles found)
        """
        cycle_kwargs = {"reference_time": reference_time}
        if window_hours is not None:
            cycle_kwargs["window_hours"] = window_hours
        raw_cycles = await self.neo4j.find_cycles(account_id, **cycle_kwargs)

        if not raw_cycles:
            return []

        persisted: List[Dict[str, Any]] = []
        seen_fingerprints: set[str] = set()

        for cycle in raw_cycles:
            node_ids   = cycle["node_ids"]
            amounts    = cycle["amounts"]
            timestamps = cycle["timestamps"]
            txn_ids    = cycle.get("txn_ids", [])

            # Simple-cycle filter (no repeated intermediates)
            if not is_simple_cycle(node_ids):
                logger.debug(
                    "Skipping non-simple cycle from %s: %s", account_id, node_ids
                )
                continue

            # Dedup — same ring found from a different seed account
            try:
                fp = cycle_fingerprint(node_ids)
            except ValueError:
                continue

            if fp in seen_fingerprints:
                continue
            seen_fingerprints.add(fp)

            # Score
            scored = score_cycle(node_ids, amounts, timestamps)
            scored["details"]["txn_ids"] = txn_ids
            scored["details"]["node_ids"] = node_ids

            # Extract the unique account IDs (drop the repeated tail)
            unique_accounts = node_ids[:-1] if node_ids[0] == node_ids[-1] else node_ids

            # Persist (idempotent — bumps detection_count on conflict)
            if self.postgres is not None:
                await self.postgres.upsert_risk_flag(
                    flag_type="CYCLE",
                    fingerprint=fp,
                    account_ids=unique_accounts,
                    risk_level=scored["risk_level"],
                    risk_score=scored["risk_score"],
                    explanation=scored["explanation"],
                    details=scored["details"],
                )

            flag = {
                "fingerprint": fp,
                "account_ids": unique_accounts,
                **scored,
            }
            persisted.append(flag)
            logger.info(
                "Cycle flag persisted | level=%s score=%.2f accounts=%s",
                scored["risk_level"],
                scored["risk_score"],
                unique_accounts,
            )

        return persisted


# ---------------------------------------------------------------------------
# Manual entrypoint: seeds a known A→B→C→A cycle and runs detection
# ---------------------------------------------------------------------------

async def _run_demo() -> None:
    """
    Inject a known three-hop cycle into Neo4j and run CycleDetector against it.
    Prints the resulting flag(s). Requires docker compose up (Postgres + Neo4j).

    Usage:
        python -m fraud.cycle_detector
    """
    logging.basicConfig(level=logging.INFO)

    neo4j_client = Neo4jClient()
    postgres_client = PostgresClient()

    await neo4j_client.initialize()
    await postgres_client.initialize()
    await neo4j_client.init_constraints()

    # Run the migration so risk_flags table exists
    migration_sql = (
        pathlib.Path(__file__).parent.parent
        / "migrations"
        / "002_create_risk_flags_table.sql"
    ).read_text()
    async with postgres_client._get_connection() as conn:
        await conn.execute(migration_sql)

    # Inject a 3-hop cycle: DEMO_A → DEMO_B → DEMO_C → DEMO_A
    # Amounts conserved (small bleed-off per hop), timestamps forward-ordered,
    # all within a 6-hour window well inside the 48h limit.
    now = datetime.now(timezone.utc)
    hops = [
        ("DEMO_A", "DEMO_B", "txn-demo-ab", 0),
        ("DEMO_B", "DEMO_C", "txn-demo-bc", 3600),    # +1h
        ("DEMO_C", "DEMO_A", "txn-demo-ca", 7200),    # +2h
    ]
    base_amount = 5_000_000  # $50,000

    for i, (src, dst, txn_id, offset_s) in enumerate(hops):
        # Amount bleeds off slightly each hop (simulates fees)
        amount = int(base_amount * (0.95 ** i))
        ts = now + timedelta(seconds=offset_s)
        await neo4j_client.upsert_transaction_graph(
            sender_id=src,
            receiver_id=dst,
            amount_cents=amount,
            timestamp_utc=ts,
            rail="WIRE",
            event_type="SETTLEMENT",
            transaction_id=txn_id,
            idempotency_key=txn_id,
        )
        print(f"  Injected {src} → {dst} | ${amount/100:,.2f} | +{offset_s//3600}h")

    print("\nRunning cycle detection seeded from DEMO_A …\n")
    detector = CycleDetector(neo4j_client, postgres_client)
    flags = await detector.detect("DEMO_A")

    if not flags:
        print("No cycles detected (check constraints and data freshness).")
    else:
        for flag in flags:
            print(f"  CYCLE FLAG [{flag['risk_level'].upper()}]")
            print(f"  Score:       {flag['risk_score']:.3f}")
            print(f"  Accounts:    {' → '.join(flag['account_ids'])}")
            print(f"  Explanation: {flag['explanation']}")
            print(f"  Fingerprint: {flag['fingerprint'][:16]}…")

    print("\nVerify in Postgres:")
    print("  SELECT flag_type, risk_level, explanation FROM risk_flags;")

    await neo4j_client.close()
    await postgres_client.close()


if __name__ == "__main__":
    asyncio.run(_run_demo())
