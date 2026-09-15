import time
from typing import Dict, Any, List

from neo4j.exceptions import ClientError

from app.db.neo4j import neo4j_client
from app.db.redis import get_redis
from app.schemas.graph import GraphElements, NodeElement, NodeData, EdgeElement, EdgeData

class GraphService:
    @staticmethod
    def _map_risk_tier(score: float) -> str:
        if score >= 0.85: return "critical"
        if score >= 0.65: return "high"
        if score >= 0.40: return "medium"
        return "low"

    @classmethod
    async def get_subgraph(cls, account_id: str, depth: int = 2, limit: int = 100) -> GraphElements:
        query = """
        MATCH (start:Account {id: $account_id})
        CALL apoc.path.subgraphAll(start, {
            maxLevel: $depth,
            relationshipFilter: "FLOWS_TO>",
            limit: $limit
        })
        YIELD nodes, relationships
        RETURN nodes, relationships
        """
        # Neo4j cannot parameterize a variable-length path bound (the `*1..N`),
        # so `depth` is interpolated here — but only after being clamped to the
        # same small integer range the API enforces, so it can never carry
        # injection. Direction (outgoing) and the LIMIT mirror the primary APOC
        # query so results don't depend on whether APOC is installed.
        safe_depth = min(max(int(depth), 1), 4)
        fallback_query = """
        MATCH path = (start:Account {id: $account_id})-[r:FLOWS_TO*1..%d]->(target:Account)
        WITH nodes(path) AS ns, relationships(path) AS rs
        LIMIT $limit
        UNWIND ns AS n
        UNWIND rs AS rel
        RETURN collect(DISTINCT n) AS nodes, collect(DISTINCT rel) AS relationships
        """ % safe_depth

        async with neo4j_client.driver.session() as session:
            try:
                result = await session.run(query, account_id=account_id, depth=depth, limit=limit)
                record = await result.single()
            except ClientError as exc:
                # Only fall back when APOC is genuinely missing; a real query
                # error must surface rather than be masked by the fallback.
                if "ProcedureNotFound" not in (exc.code or ""):
                    raise
                result = await session.run(fallback_query, account_id=account_id, limit=limit)
                record = await result.single()

            if not record or not record["nodes"]:
                return GraphElements(nodes=[], edges=[])

            nodes_out: List[NodeElement] = []
            edges_out: List[EdgeElement] = []
            seen_nodes = set()
            seen_edges = set()

            for node in record["nodes"]:
                props = dict(node)
                nid = props.get("id", str(node.id))
                if nid in seen_nodes:
                    continue
                seen_nodes.add(nid)
                
                score = float(props.get("risk_score", 0.0))
                nodes_out.append(NodeElement(
                    data=NodeData(
                        id=nid,
                        label=props.get("label", nid[:8]),
                        node_type=props.get("node_type", "account"),
                        risk_score=score,
                        risk_tier=cls._map_risk_tier(score),
                        community_id=props.get("community_id"),
                        pagerank_score=props.get("pagerank_score", 0.0),
                        attributes={k: v for k, v in props.items() if k not in ["id", "risk_score"]}
                    )
                ))

            for rel in record["relationships"]:
                rid = f"{rel.start_node['id']}->{rel.end_node['id']}"
                if rid in seen_edges:
                    continue
                seen_edges.add(rid)
                
                props = dict(rel)
                total_amt = float(props.get("total_amount", 0.0))
                edges_out.append(EdgeElement(
                    data=EdgeData(
                        id=rid,
                        source=rel.start_node["id"],
                        target=rel.end_node["id"],
                        tx_count=props.get("tx_count", 1),
                        total_amount=total_amt,
                        first_ts=props.get("first_ts"),
                        last_ts=props.get("last_ts"),
                        weight=max(1.0, min(10.0, total_amt / 100000.0))
                    )
                ))

            return GraphElements(nodes=nodes_out, edges=edges_out)

    @classmethod
    async def get_shortest_path(cls, account_a: str, account_b: str) -> GraphElements:
        query = """
        MATCH (a:Account {id: $account_a}), (b:Account {id: $account_b})
        MATCH p = shortestPath((a)-[:FLOWS_TO*..10]->(b))
        RETURN nodes(p) AS nodes, relationships(p) AS relationships
        """
        async with neo4j_client.driver.session() as session:
            result = await session.run(query, account_a=account_a, account_b=account_b)
            record = await result.single()

            if not record:
                return GraphElements(nodes=[], edges=[])

            nodes_out = [
                NodeElement(data=NodeData(
                    id=dict(n)["id"],
                    label=dict(n)["id"][:8],
                    risk_score=float(dict(n).get("risk_score", 0.0)),
                    risk_tier=cls._map_risk_tier(float(dict(n).get("risk_score", 0.0)))
                )) for n in record["nodes"]
            ]
            edges_out = [
                EdgeElement(data=EdgeData(
                    id=f"{r.start_node['id']}->{r.end_node['id']}",
                    source=r.start_node["id"],
                    target=r.end_node["id"],
                    total_amount=float(dict(r).get("total_amount", 0.0)),
                    tx_count=dict(r).get("tx_count", 1)
                )) for r in record["relationships"]
            ]
            return GraphElements(nodes=nodes_out, edges=edges_out)

    @staticmethod
    async def get_flow_between(account_a: str, account_b: str, window: str = "7d") -> Dict[str, Any]:
        seconds_map = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}
        window_seconds = seconds_map.get(window, 604800)
        min_ts = time.time() - window_seconds
        
        redis = get_redis()
        key = f"edge:{account_a}:{account_b}"
        tx_entries = await redis.zrangebyscore(key, min=min_ts, max="+inf")
        
        total_vol = 0.0
        tx_count = len(tx_entries)
        for tx in tx_entries:
            try:
                total_vol += float(tx.split(":")[0])  # amount_cents:txn_id
            except (ValueError, IndexError):
                total_vol += 0.0
                
        return {
            "source": account_a,
            "target": account_b,
            "window": window,
            "total_volume_cents": total_vol,
            "tx_count": tx_count,
            "avg_amount_cents": (total_vol / tx_count) if tx_count > 0 else 0.0,
            "path_count": 1 if tx_count > 0 else 0
        }