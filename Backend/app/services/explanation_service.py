"""Natural-language risk explanations (spec §7).

Evidence about an account is gathered from the stores (pure `build_evidence`
turns raw rows into an `AccountEvidence`), then a provider turns it into a
schema-validated `ModelJSON`:

    ollama     — a local model via the Ollama HTTP API (free, default when reachable)
    anthropic  — the Claude API (opt-in: needs ANTHROPIC_API_KEY)
    none       — deterministic text from the signals; also the fallback whenever a
                 provider errors or returns something off-schema

Nothing here ever raises to the caller for lack of a model.
"""
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from pydantic import ValidationError

from app.schemas.api import ExplanationOut, ModelJSON, TYPOLOGIES

logger = logging.getLogger("explain")

DEFAULT_MODELS = {"anthropic": "claude-opus-5", "ollama": "llama3.2"}

SYSTEM_PROMPT = (
    "You are a financial-crime analyst writing a short, plain-English risk explanation "
    "for one account in a payment network, for a compliance reviewer. Use ONLY the "
    "evidence provided. Do not invent transactions, amounts, parties or facts. If the "
    "evidence is weak, say so and lower your confidence. Money-laundering typologies "
    "you may name: " + ", ".join(TYPOLOGIES) + ". Respond with a JSON object with exactly "
    "these keys: risk_level (low|medium|high|critical), confidence (0-1), explanation "
    "(<=120 words), detected_typology (one of the typologies or null), "
    "compliance_summary (one sentence for the audit log)."
)


# --- evidence ------------------------------------------------------------------

@dataclass
class AccountEvidence:
    account_id: str
    gnn_risk_score: Optional[float]
    gnn_risk_tier: Optional[str]
    in_cycle: bool
    community_id: Optional[str]
    pagerank_score: float
    payers: List[Dict[str, Any]] = field(default_factory=list)   # {id, amount, n, tier}
    payees: List[Dict[str, Any]] = field(default_factory=list)
    flags: List[Dict[str, Any]] = field(default_factory=list)    # {flag_type, risk_level, explanation}
    dataset: Dict[str, Any] = field(default_factory=dict)


def build_evidence(node: Dict[str, Any], payers: List[Dict[str, Any]], payees: List[Dict[str, Any]],
                   flags: List[Dict[str, Any]], dataset: Dict[str, Any]) -> AccountEvidence:
    gnn = node.get("gnn_risk_score")
    return AccountEvidence(
        account_id=str(node.get("id")),
        gnn_risk_score=float(gnn) if gnn is not None else None,
        gnn_risk_tier=node.get("gnn_risk_tier"),
        in_cycle=bool(node.get("in_cycle", False)),
        community_id=(str(node["community_id"]) if node.get("community_id") is not None else None),
        pagerank_score=float(node.get("pagerank_score") or 0.0),
        payers=[dict(p) for p in payers],
        payees=[dict(p) for p in payees],
        flags=[{"flag_type": f.get("flag_type"), "risk_level": f.get("risk_level"),
                "explanation": f.get("explanation")} for f in flags],
        dataset={"name": dataset.get("name"), "labelled": bool(dataset.get("labelled", False))},
    )


def evidence_prompt(ev: AccountEvidence) -> str:
    """The user turn. Deliberately carries no ground-truth label or typology —
    the model must reason from structure and signals only."""
    body = {
        "account_id": ev.account_id,
        "gnn_risk_score": ev.gnn_risk_score,
        "gnn_risk_tier": ev.gnn_risk_tier,
        "on_detected_cycle": ev.in_cycle,
        "community_id": ev.community_id,
        "pagerank_score": ev.pagerank_score,
        "top_payers": ev.payers,
        "top_payees": ev.payees,
        "open_flags": ev.flags,
        "dataset": ev.dataset.get("name"),
    }
    return "Evidence:\n" + json.dumps(body, indent=1, sort_keys=True) + "\nWrite the explanation JSON."


# --- rule-based provider (also the fallback) -------------------------------------

def _tier_from_score(score: float) -> str:
    if score >= 0.85: return "critical"
    if score >= 0.65: return "high"
    if score >= 0.40: return "medium"
    return "low"


def rule_based(ev: AccountEvidence) -> ModelJSON:
    score = ev.gnn_risk_score or 0.0
    level = ev.gnn_risk_tier or _tier_from_score(score)
    typology: Optional[str] = None
    parts: List[str] = []
    if ev.in_cycle:
        level, typology = "critical", "CYCLE"
        parts.append("The account sits on a detected circular flow of funds.")
    parts.append("The graph model scores it %.2f (%s risk)." % (score, level))
    n_in, n_out = len(ev.payers), len(ev.payees)
    if typology is None and n_out >= 5 and n_out > 2 * max(n_in, 1):
        typology = "FAN-OUT"
        parts.append("It disperses funds to %d counterparties." % n_out)
    elif typology is None and n_in >= 5 and n_in > 2 * max(n_out, 1):
        typology = "FAN-IN"
        parts.append("It collects funds from %d counterparties." % n_in)
    risky = [p["id"] for p in ev.payers + ev.payees if p.get("tier") in ("high", "critical")]
    if risky:
        parts.append("It transacts with %d elevated-risk counterpart%s." % (len(risky), "y" if len(risky) == 1 else "ies"))
    for f in ev.flags[:2]:
        if f.get("explanation"):
            parts.append(str(f["explanation"]))
    confidence = 0.95 if ev.in_cycle else min(0.9, 0.5 + abs(score - 0.45))
    return ModelJSON(
        risk_level=level if level in ("low", "medium", "high", "critical") else "low",
        confidence=round(confidence, 2),
        explanation=" ".join(parts)[:1200],
        detected_typology=typology,
        compliance_summary="Rule-based summary from GNN score, cycle membership and counterparty tiers.",
    )


# --- providers -------------------------------------------------------------------

def resolve_provider(provider_env: str, api_key: str, ollama_reachable: bool) -> str:
    p = (provider_env or "").strip().lower()
    if p in ("anthropic", "ollama", "none"):
        return p
    if api_key:
        return "anthropic"
    if ollama_reachable:
        return "ollama"
    return "none"


async def check_ollama(base_url: str, timeout: float = 1.0) -> bool:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(base_url.rstrip("/") + "/api/tags")
            return r.status_code == 200
    except Exception:  # noqa: BLE001 — unreachable is a normal state
        return False


def _parse_model_json(text: str) -> ModelJSON:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):text.rfind("}") + 1]
    return ModelJSON.model_validate_json(text)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, api_key: str) -> None:
        import anthropic
        self.model = model
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def generate(self, ev: AccountEvidence) -> ModelJSON:
        system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
        messages = [{"role": "user", "content": evidence_prompt(ev)}]
        parse = getattr(self._client.messages, "parse", None)
        if parse is not None:
            resp = await parse(model=self.model, max_tokens=1024, system=system,
                               messages=messages, output_format=ModelJSON)
            parsed = getattr(resp, "parsed_output", None)
            if parsed is not None:
                return parsed
        resp = await self._client.messages.create(model=self.model, max_tokens=1024,
                                                  system=system, messages=messages)
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        return _parse_model_json(text)


class OllamaProvider:
    name = "ollama"

    def __init__(self, model: str, base_url: str) -> None:
        import ollama
        self.model = model
        self._client = ollama.AsyncClient(host=base_url)

    async def generate(self, ev: AccountEvidence) -> ModelJSON:
        resp = await self._client.chat(
            model=self.model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": evidence_prompt(ev)}],
            format=ModelJSON.model_json_schema(),
            options={"temperature": 0.2},
        )
        content = resp["message"]["content"]
        return _parse_model_json(content)


# --- the service -------------------------------------------------------------------

_EVIDENCE_Q = (
    "MATCH (a:Account {id: $id}) "
    "CALL (a) { MATCH (p:Account)-[r:FLOWS_TO]->(a) WHERE p <> a "
    "  WITH p, r ORDER BY r.total_amount DESC LIMIT 5 "
    "  RETURN collect({id: p.id, amount: r.total_amount, n: r.tx_count, tier: p.gnn_risk_tier}) AS payers } "
    "CALL (a) { MATCH (a)-[r:FLOWS_TO]->(q:Account) WHERE q <> a "
    "  WITH q, r ORDER BY r.total_amount DESC LIMIT 5 "
    "  RETURN collect({id: q.id, amount: r.total_amount, n: r.tx_count, tier: q.gnn_risk_tier}) AS payees } "
    "RETURN a, payers, payees"
)


class ExplanationService:
    def __init__(self, session_factory: Callable[[], Any], pg_factory: Callable[[], Any],
                 provider: Optional[Any] = None) -> None:
        self._session_factory = session_factory
        self._pg_factory = pg_factory
        self.provider = provider          # object with .name, .model, async generate(ev)

    def status(self) -> Dict[str, Any]:
        if self.provider is None:
            return {"provider": "none", "model": None, "reachable": False}
        return {"provider": self.provider.name, "model": self.provider.model, "reachable": True}

    async def gather(self, account_id: str) -> Optional[AccountEvidence]:
        session = self._session_factory()
        async with session() as s:
            rec = await (await s.run(_EVIDENCE_Q, id=account_id)).single()
        if not rec:
            return None
        pg = self._pg_factory()
        flags: List[Dict[str, Any]] = []
        if pg is not None:
            rows = await pg.get_risk_flags(status="open", limit=200)
            flags = [r for r in rows if account_id in (r.get("account_ids") or [])][:5]
        from app.services.stats_service import cache
        dataset = cache.overview()["dataset"] if cache.ready() else {}
        return build_evidence(dict(rec["a"]), list(rec["payers"] or []), list(rec["payees"] or []),
                              flags, dataset)

    async def explain(self, account_id: str) -> ExplanationOut:
        ev = await self.gather(account_id)
        if ev is None:
            ev = AccountEvidence(account_id=account_id, gnn_risk_score=None, gnn_risk_tier=None,
                                 in_cycle=False, community_id=None, pagerank_score=0.0)
            return self._wrap(ev, rule_based(ev), "none", None)
        if self.provider is None:
            return self._wrap(ev, rule_based(ev), "none", None)
        try:
            result = await self.provider.generate(ev)
            if result.detected_typology not in TYPOLOGIES:
                result.detected_typology = None
            return self._wrap(ev, result, self.provider.name, self.provider.model)
        except (ValidationError, ValueError, Exception) as exc:  # noqa: BLE001
            logger.warning("explanation provider %s failed for %s: %s", self.provider.name, account_id, exc)
            return self._wrap(ev, rule_based(ev), "none", None)

    @staticmethod
    def _wrap(ev: AccountEvidence, result: ModelJSON, provider: str, model: Optional[str]) -> ExplanationOut:
        return ExplanationOut(account_id=ev.account_id, provider=provider, model=model,
                              generated_at=int(time.time()), **result.model_dump())


service = ExplanationService(lambda: None, lambda: None, provider=None)


async def configure(settings: Any) -> None:
    """Pick the provider once at startup and wire the store factories."""
    from app.db.neo4j import neo4j_client
    from app.viz import deps as viz_deps
    reachable = await check_ollama(settings.OLLAMA_BASE_URL)
    name = resolve_provider(settings.LLM_PROVIDER, settings.ANTHROPIC_API_KEY, reachable)
    model = settings.LLM_MODEL or DEFAULT_MODELS.get(name, "")
    provider: Optional[Any] = None
    try:
        if name == "anthropic":
            provider = AnthropicProvider(model, settings.ANTHROPIC_API_KEY)
        elif name == "ollama" and reachable:
            provider = OllamaProvider(model, settings.OLLAMA_BASE_URL)
    except Exception as exc:  # noqa: BLE001 — a missing SDK degrades to rule-based
        logger.warning("could not initialise %s provider (%s); using rule-based", name, exc)
        provider = None
    service._session_factory = lambda: neo4j_client.driver.session
    service._pg_factory = viz_deps.pg
    service.provider = provider
    logger.info("explanation provider: %s", service.status())
