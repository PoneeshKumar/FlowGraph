"""Per-transaction reads over Neo4j TRANSFER edges (spec §6.6).

Postgres has no transactions table for the bulk-loaded graph, so this is the
only source of transaction-level detail. `session` is the zero-arg session
factory convention used by app.viz.store.
"""
from typing import Any, Callable, Dict, List, Optional

from app.services.stats_service import currency_code

TIER_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

_RETURN = (
    "RETURN t.txn_id AS txn_id, s.id AS sender_id, r.id AS receiver_id, "
    "t.amount_cents AS amount_cents, t.rail AS rail, t.event_type AS event_type, t.ts AS ts, "
    "s.gnn_risk_tier AS sender_tier, r.gnn_risk_tier AS receiver_tier "
    "ORDER BY t.ts DESC LIMIT $limit"
)


def max_tier(a: Optional[str], b: Optional[str]) -> str:
    ra, rb = TIER_RANK.get(a or "low", 0), TIER_RANK.get(b or "low", 0)
    return (a if ra >= rb else b) or "low"


def shape_transaction(rec: Dict[str, Any], is_flagged: Callable[[str], bool]) -> Dict[str, Any]:
    sender, receiver = rec["sender_id"], rec["receiver_id"]
    s_tier, r_tier = rec.get("sender_tier") or "low", rec.get("receiver_tier") or "low"
    return {
        "txn_id": str(rec["txn_id"]),
        "sender_id": sender,
        "receiver_id": receiver,
        "amount_cents": int(rec.get("amount_cents") or 0),
        "currency": currency_code(rec.get("rail") or ""),
        "rail": rec.get("rail") or "",
        "event_type": rec.get("event_type"),
        "ts": int(rec.get("ts") or 0),
        "sender_tier": s_tier,
        "receiver_tier": r_tier,
        "risk_tier": max_tier(s_tier, r_tier),
        "flagged": bool(is_flagged(sender) or is_flagged(receiver)),
    }


def _flag_lookup() -> Callable[[str], bool]:
    from app.services.stats_service import cache
    return cache.is_flagged if cache.ready() else (lambda _a: False)


async def list_latest(session: Callable[[], Any], limit: int, rail: Optional[str]) -> List[Dict[str, Any]]:
    # `t.ts IS NOT NULL` is what lets the planner serve ORDER BY from the
    # transfer_ts range index; without the predicate it sorts all 5M edges
    # (measured: 8.1 s → 0.08 s).
    if rail:
        query = ("MATCH (s:Account)-[t:TRANSFER]->(r:Account) "
                 "WHERE t.ts IS NOT NULL AND t.rail = $rail " + _RETURN)
        params: Dict[str, Any] = {"limit": limit, "rail": rail}
    else:
        query = "MATCH (s:Account)-[t:TRANSFER]->(r:Account) WHERE t.ts IS NOT NULL " + _RETURN
        params = {"limit": limit}
    flagged = _flag_lookup()
    async with session() as s:
        res = await s.run(query, **params)
        return [shape_transaction(dict(r), flagged) async for r in res]


async def list_for_account(session: Callable[[], Any], account_id: str, limit: int,
                           rail: Optional[str]) -> List[Dict[str, Any]]:
    where = "WHERE t.rail = $rail " if rail else ""
    query = (
        "MATCH (a:Account {id: $id})-[t:TRANSFER]-(o:Account) " + where +
        "WITH t, startNode(t) AS s, endNode(t) AS r " + _RETURN
    )
    params: Dict[str, Any] = {"id": account_id, "limit": limit}
    if rail:
        params["rail"] = rail
    flagged = _flag_lookup()
    async with session() as s:
        res = await s.run(query, **params)
        return [shape_transaction(dict(r), flagged) async for r in res]
