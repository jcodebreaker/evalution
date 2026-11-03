"""A tiny LangGraph agent for evalution's langgraph adapter: it remembers facts about the
person in the store (``["memories", <user_id>]``) and folds older messages into a summary.

Served by ``langgraph dev`` (see README.md); the user comes from ``configurable.user_id``.
"""

import os
import uuid

from langchain_core.messages import RemoveMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.config import get_config, get_store
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from openinference.instrumentation.langchain import LangChainInstrumentor
from phoenix.otel import register

# Spans go to PHOENIX_COLLECTOR_ENDPOINT, project PHOENIX_PROJECT_NAME. OpenInference sets
# session.id from the run's thread_id, which is how evalution finds a try's spans.
LangChainInstrumentor().instrument(tracer_provider=register())

KEEP = 6  # messages kept as they are; older ones are folded into the summary


class State(MessagesState):
    summary: str


def _namespace() -> tuple[str, str]:
    return ("memories", str(get_config()["configurable"].get("user_id") or "anonymous"))


@tool
def remember(fact: str) -> str:
    """Save one short fact about the person (a preference, a name, a decision)."""
    get_store().put(_namespace(), str(uuid.uuid4()), {"text": fact})
    return "Saved."


model = ChatOpenAI(model=os.environ.get("MODEL", "llama3.2")).bind_tools([remember])


def agent(state: State) -> dict:
    memories = [item.value["text"] for item in get_store().search(_namespace(), limit=20)]
    system = "You are a helpful assistant. Use the remember tool for lasting facts."
    if memories:
        system += "\nWhat you know about the person:\n- " + "\n- ".join(memories)
    if state.get("summary"):
        system += f"\nThe conversation so far, in short: {state['summary']}"
    reply = model.invoke([SystemMessage(system), *state["messages"]])
    return {"messages": [reply]}


def summarise(state: State) -> dict:
    """Fold everything but the last :data:`KEEP` messages into the summary."""
    messages = state["messages"]
    if len(messages) <= KEEP:
        return {}
    cut = len(messages) - KEEP
    while cut < len(messages) and messages[cut].type == "tool":  # keep a tool call whole
        cut += 1
    older = messages[:cut]
    ask = (
        f"Summary so far: {state.get('summary') or 'none'}\n"
        "Extend it with these messages, keeping every decision and fact:\n"
        + "\n".join(f"{m.type}: {m.content}" for m in older)
    )
    summary = ChatOpenAI(model=os.environ.get("MODEL", "llama3.2")).invoke(ask).content
    return {"summary": summary, "messages": [RemoveMessage(id=m.id) for m in older]}


builder = StateGraph(State)
builder.add_node("summarise", summarise)
builder.add_node("agent", agent)
builder.add_node("tools", ToolNode([remember]))
builder.add_edge(START, "summarise")
builder.add_edge("summarise", "agent")
builder.add_conditional_edges("agent", tools_condition)
builder.add_edge("tools", "agent")
graph = builder.compile()
