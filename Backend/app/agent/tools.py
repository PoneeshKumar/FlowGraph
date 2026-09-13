from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.agent.helpers import summarize_graph, evaluate_risk, summarize_risky_applications
from app.schemas.graph import GraphElements
from app.services.risk_aggregator import RiskVerdict

ollama_model = OpenAIModel(
    'llama3.2',
    provider=OpenAIProvider(base_url='http://localhost:11434/v1'),
)

agent = Agent(
    ollama_model,
    system_prompt=(
        "You are a helpful assistant that answers questions using the provided "
        "business risk data. If you do not know the answer, say so instead of "
        "inventing facts."
    ),
)

# the tools that the agent can use to answer questions are below and the "context/information" that the agent can use to asnwer questions is given fromm the other parts of the codebase


# agent to summarize the graph
@agent.tool(
    name="summarize_graph",
    description="Summarizes the graph for a given account_id, depth, and limit. Returns the subgraph elements.",
)
async def summarize_graph_tool(account_id: str, depth: int, limit: int) -> GraphElements:
    return await summarize_graph(account_id, depth, limit)

@agent.tool(
    name="summarize_risky_applications",
    description="Summarizes all risky applications/accounts across the business, ordered by risk score.",
)
async def summarize_risky_applications_tool(risk_tier: str, limit: int) -> dict:
    return await summarize_risky_applications(risk_tier, limit)

# agent to evaluate the risk of an account
@agent.tool(
    name="evaluate_risk",
    description="Evaluates the risk of an account based on the provided parameters. Returns a RiskVerdict.",
)
async def evaluate_risk_tool(account_id: str, gnn_score: float, has_cycle: bool, cycle_length: int | None) -> RiskVerdict:
    return await evaluate_risk(account_id, gnn_score, has_cycle, cycle_length)