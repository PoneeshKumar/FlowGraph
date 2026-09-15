import time
from typing import Dict, Any, List, Optional

from neo4j.exceptions import ClientError

from app.db.neo4j import neo4j_client
from app.schemas.graph import GraphElements, NodeElement, NodeData, EdgeElement, EdgeData

_HIDDEN = {"id", "risk_score", "risk_tier", "gnn_risk_score", "gnn_risk_tier", "in_cycle",
           "community_id", "pagerank_score", "label", "node_type"}


class GraphService:
    @staticmethod
    def _map_risk_tier(score: float) -> str:
        if score >= 0.85: return "critical"
        if score >= 0.65: return "high"
        if score >= 0.40: return "medium"
        return "low"

    @classmethod
    def node_data(cls, props: Dict[str, Any]) -> NodeData:
        """One rule for turning stored Account properties into API node data.
        Real accounts carry gnn_* properties; risk_score/risk_tier exist only
        once the aggregator has written a verdict, and take precedence then."""
        from app.viz import threshold
        nid = str(props.get("id"))
        gnn = props.get("gnn_risk_score")
        gnn = float(gnn) if gnn is not None else None
        score = props.get("risk_score")
        score = float(score) if score is not None else (gnn if gnn is not None else 0.0)
        tier = props.get("risk_tier") or props.get("gnn_risk_tier") or cls._map_risk_tier(score)
        in_cycle = bool(props.get("in_cycle", False))
        return NodeData(
            id=nid,
            label=str(props.get("label") or nid[:8]),
            node_type=str(props.get("node_type") or "account"),
            risk_score=score,
            risk_tier=str(tier),
            gnn_risk_score=gnn,
            gnn_risk_tier=props.get("gnn_risk_tier"),
            in_cycle=in_cycle,
            marked=threshold.is_marked(gnn, in_cycle, threshold.model_threshold()),
            community_id=(str(props["community_id"]) if props.get("community_id") is not None else None),
            pagerank_score=float(props.get("pagerank_score") or 0.0),
            attributes={k: v for k, v in props.items() if k not in _HIDDEN},
        )

    @classmethod
    async def get_account(cls, account_id: str) -> Optional[NodeData]:
        async with neo4j_client.driver.session() as session:
            rec = await (await session.run(
                "MATCH (a:Account {id: $id}) RETURN a", id=account_id)).single()
        return cls.node_data(dict(rec["a"])) if rec else None

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
                
                nodes_out.append(NodeElement(data=cls.node_data(props)))

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

            nodes_out = [NodeElement(data=cls.node_data(dict(n))) for n in record["nodes"]]
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
        """Volume a→b inside a window, read from TRANSFER edges. The window counts
        back from the loaded dataset's activity anchor (the data is historical);
        wall-clock only when no stats are loaded."""
        from app.services.stats_service import cache
        seconds_map = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}
        anchor = cache.anchor_ts() if cache.ready() else int(time.time())
        min_ts = anchor - seconds_map.get(window, 604800)
        query = (
            "MATCH (a:Account {id: $a})-[t:TRANSFER]->(b:Account {id: $b}) "
            "WHERE t.ts >= $min_ts "
            "RETURN count(t) AS n, coalesce(sum(t.amount_cents), 0) AS total"
        )
        async with neo4j_client.driver.session() as session:
            rec = await (await session.run(query, a=account_a, b=account_b, min_ts=min_ts)).single()
        n = int(rec["n"]) if rec else 0
        total = float(rec["total"]) if rec else 0.0
        return {
            "source": account_a, "target": account_b, "window": window,
            "total_volume_cents": total, "tx_count": n,
            "avg_amount_cents": (total / n) if n else 0.0,
            "path_count": 1 if n else 0,
        }
